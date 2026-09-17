"""FastAPI application: WS endpoints, REST, static, preview proxy.

Composed by :func:`create_app`. The app owns a single :class:`Database`,
:class:`NodeRegistry`, :class:`JobsStore`, :class:`HandleBook`, and
:class:`FrontendHub`; these are all attached to ``app.state``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

import aiosqlite
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from hololab import PROTOCOL_V_MAX, PROTOCOL_V_MIN
from hololab.gateway.execution import (
    DispatchError,
    WorkflowRunError,
    _resolve_origin_job_id,
    dispatch_graph_node,
    run_snapshot,
)
from hololab.gateway.handles import Handle, HandleBook
from hololab.gateway.hub import FrontendHub
from hololab.gateway.jobs import Job, JobState, JobStateMachine, event_from_transition
from hololab.gateway.models import (
    HandleInfo as HandleInfoOut,
)
from hololab.gateway.models import (
    HandleSummary,
    HealthResponse,
    JobDetail,
    JobRow,
    LogTail,
    NodeInfo,
    OverviewResponse,
    RestoreResult,
    RunSummary,
    WorkflowDeleteResult,
    WorkflowRunResult,
    WorkflowSaveResult,
)
from hololab.gateway.models import (
    SnapshotDetail as SnapshotDetailOut,
)
from hololab.gateway.models import (
    WorkflowDetail as WorkflowDetailOut,
)
from hololab.gateway.models import (
    WorkflowSummary as WorkflowSummaryOut,
)
from hololab.gateway.proxy import proxy_get, strip_workspace_prefix
from hololab.gateway.registry import (
    JobsStore,
    LogStore,
    NodeAuthError,
    NodeRegistry,
    SnapshotJobsStore,
    mark_stuck_jobs_interrupted,
    reset_all_online_flags,
)
from hololab.gateway.workflows import (
    PackHandle,
    WorkflowGraph,
    WorkflowStore,
    agent_graph_dict,
    issues_to_json,
    validate_snapshot,
)
from hololab.logging import get_logger
from hololab.paths import frontend_dist_dir, gateway_sqlite_path
from hololab.persistence.db import open_database
from hololab.protocol import (
    ArtifactDeleteResp,
    HandleCheckResp,
    HandleLocateReq,
    HandleLocateResp,
    HandleRegister,
    Heartbeat,
    JobAck,
    JobAssign,
    JobDone,
    JobFail,
    JobLog,
    JobProgress,
    JobUpdate,
    LogChunk,
    NodeConfigGetReq,
    NodeConfigGetResp,
    NodeConfigSetReq,
    NodeConfigSetResp,
    NodeOffline,
    NodeOnline,
    PacksUpdated,
    Register,
    RegisterErr,
    RegisterOk,
    decode,
    encode,
    negotiate_version,
)
from hololab.protocol.messages import JobFailReason, PackInventoryEntry

log = get_logger("gateway")

# Heartbeat timeout — see docs/architecture.md#connection-topology.
HEARTBEAT_DEAD_SECONDS = 45.0
HEARTBEAT_SWEEP_INTERVAL = 5.0


def create_app(*, db_path: Path | None = None) -> FastAPI:
    """Construct the FastAPI app. Called by CLI or tests.

    When ``HOLOLAB_DEV=1`` is set in the environment, the ``/`` static mount
    is skipped so that a stale frontend/dist never masks a running Vite dev
    server at :5173. In dev mode you always browse to :5173, not :8828.
    """

    app = FastAPI(title="HoloLab Gateway", version="0.0.1")

    dev_mode = os.environ.get("HOLOLAB_DEV") == "1"
    app.state.dev_mode = dev_mode

    @app.on_event("startup")
    async def _startup() -> None:
        path = db_path or gateway_sqlite_path()
        db = await open_database(path)
        await reset_all_online_flags(db)
        # Any job that was in-flight when the previous gateway process
        # died can't be reconciled — flip it to ``interrupted`` before
        # the frontend or API sees it, so the UI never shows a fake
        # eternal-blue running node. See jobs.py for the state semantics.
        interrupted_count = await mark_stuck_jobs_interrupted(db)
        if interrupted_count:
            log.info("startup sweep marked jobs as interrupted", count=interrupted_count)

        app.state.db = db
        app.state.registry = NodeRegistry(db)
        app.state.jobs_store = JobsStore(db)
        app.state.snapshot_jobs = SnapshotJobsStore(db)
        app.state.logs = LogStore(db)
        app.state.handles = HandleBook(db)
        app.state.workflows = WorkflowStore(db)
        app.state.hub = FrontendHub()
        # Correlation futures for gateway↔node request/response frames.
        # Keys: req_id (str); values: the future the REST handler awaits.
        # Populated when the gateway sends a request frame, drained by
        # _dispatch_node_frame when the matching _resp arrives.
        app.state.pending_config_replies: dict[str, asyncio.Future[Any]] = {}
        app.state.pending_handle_checks: dict[str, asyncio.Future[Any]] = {}
        app.state.pending_artifact_deletes: dict[str, asyncio.Future[Any]] = {}

        app.state.heartbeat_task = asyncio.create_task(
            _heartbeat_sweeper(app), name="hololab-heartbeat-sweeper"
        )
        log.info(
            "gateway started",
            db=str(path),
            frontend=("dev-mode (Vite at :5173)" if dev_mode else _frontend_status()),
        )

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        task = getattr(app.state, "heartbeat_task", None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        db = getattr(app.state, "db", None)
        if db is not None:
            await db.close()
        log.info("gateway stopped")

    # Routes MUST be registered before the ``/`` static mount, since starlette
    # dispatches in registration order and a ``Mount("/")`` catches every path.
    _mount_routes(app)
    if not dev_mode:
        _mount_static(app)
    else:
        _mount_dev_landing(app)
    return app


def _frontend_status() -> str:
    """Short string describing whether a built frontend is present."""

    return "bundled dist" if frontend_dist_dir() is not None else "no dist (placeholder page)"


# ---------------------------------------------------------------------------
# Static + placeholder
# ---------------------------------------------------------------------------


def _mount_static(app: FastAPI) -> None:
    """Mount the built React app at ``/``. Fall back to a placeholder in dev."""

    dist = frontend_dist_dir()
    if dist is not None:
        # Static mount must come AFTER the /api and /ws routes; StaticFiles
        # gets registered here but resolved after specific routes.
        app.mount(
            "/",
            StaticFiles(directory=str(dist), html=True),
            name="frontend",
        )
    else:

        @app.get("/", response_class=HTMLResponse)
        async def _placeholder() -> str:  # pragma: no cover — trivial
            return _PLACEHOLDER_HTML


def _mount_dev_landing(app: FastAPI) -> None:
    """In HOLOLAB_DEV=1 mode, browsing :8828 shows a helper page pointing to Vite."""

    @app.get("/", response_class=HTMLResponse)
    async def _dev_landing() -> str:  # pragma: no cover — trivial
        return _DEV_LANDING_HTML


_DEV_LANDING_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>HoloLab (dev)</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 640px; margin: 4em auto; padding: 0 1em; color: #333; }
  code { background: #f2f2f2; padding: 2px 5px; border-radius: 3px; }
  a { color: #06c; font-weight: 600; }
</style></head>
<body>
  <h1>HoloLab is running in dev mode.</h1>
  <p>The gateway is on <code>:8828</code>. For UI development, open the Vite dev server:</p>
  <p><a href="http://127.0.0.1:5173">http://127.0.0.1:5173</a></p>
  <p>Vite proxies <code>/api</code>, <code>/proxy</code>, and <code>/ws</code> back to this gateway.
  Restart <code>hololab dev</code> without <code>HOLOLAB_DEV=1</code> to serve the bundled dist here instead.</p>
</body></html>
"""


_PLACEHOLDER_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>HoloLab</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 720px; margin: 4em auto; padding: 0 1em; color: #333; }
  code { background: #f2f2f2; padding: 2px 5px; border-radius: 3px; }
  a { color: #06c; }
</style></head>
<body>
  <h1>HoloLab gateway is running.</h1>
  <p>The frontend has not been built. From the repo root, run:</p>
  <pre><code>cd hololab/frontend
npm install
npm run build</code></pre>
  <p>Then restart the gateway.</p>
  <p>API endpoints:
    <a href="/api/health">/api/health</a> ·
    <a href="/api/nodes">/api/nodes</a> ·
    <a href="/api/jobs">/api/jobs</a>
  </p>
</body></html>
"""


# ---------------------------------------------------------------------------
# Routes: REST + WS
# ---------------------------------------------------------------------------


def _mount_routes(app: FastAPI) -> None:
    @app.get(
        "/api/health",
        response_model=HealthResponse,
        tags=["meta"],
        summary="Liveness + protocol version probe.",
    )
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": "0.0.1",
            "protocol_v_min": PROTOCOL_V_MIN,
            "protocol_v_max": PROTOCOL_V_MAX,
            "ts": time.time(),
        }

    @app.get(
        "/api/overview",
        response_model=OverviewResponse,
        tags=["meta"],
        summary="One-call system snapshot for agents / dashboards.",
    )
    async def overview() -> dict[str, Any]:
        """Aggregates ``/api/health`` + ``/api/nodes`` + workflow rollup
        + recent-job counts. An agent asking "what's the state of my
        system right now?" should hit this endpoint first."""

        registry: NodeRegistry = app.state.registry
        workflows_store: WorkflowStore = app.state.workflows
        jobs_store: JobsStore = app.state.jobs_store

        nodes = await registry.as_summary_json()

        # Workflow counts by last-run rollup — reuses list_workflows shape.
        drafts = await workflows_store.list_drafts()
        by_state: dict[str, int] = {}
        for row in drafts:
            snapshots = await workflows_store.list_snapshots_for_workflow(row["workflow_id"])
            if not snapshots:
                by_state["never_run"] = by_state.get("never_run", 0) + 1
                continue
            jobs = await jobs_store.list_by_snapshot(snapshots[0].snapshot_id)
            if not jobs:
                rollup = "pending"
            elif any(j["state"] == "failed" for j in jobs):
                rollup = "failed"
            elif all(j["state"] == "done" for j in jobs):
                rollup = "done"
            elif any(j["state"] in ("running", "assigned") for j in jobs):
                rollup = "running"
            else:
                rollup = "pending"
            by_state[rollup] = by_state.get(rollup, 0) + 1

        recent = await jobs_store.list_recent(limit=20)
        counts_by_state: dict[str, int] = {}
        for j in recent:
            counts_by_state[j["state"]] = counts_by_state.get(j["state"], 0) + 1

        return {
            "version": "0.0.1",
            "ts": time.time(),
            "nodes": nodes,
            "workflows": {"total": len(drafts), "by_last_run_state": by_state},
            "jobs": {"counts_by_state": counts_by_state, "recent": recent},
        }

    @app.get(
        "/api/nodes",
        response_model=list[NodeInfo],
        tags=["nodes"],
        summary="Currently-connected compute nodes with GPU and pack inventory.",
    )
    async def list_nodes() -> list[dict[str, Any]]:
        registry: NodeRegistry = app.state.registry
        return await registry.as_summary_json()

    @app.get(
        "/api/nodes/{node_id}/config",
        tags=["nodes"],
        summary="Read the node's live effective config (workspace + file server + …).",
    )
    async def get_node_config(node_id: str) -> dict[str, Any]:
        """Forwards a ``node_config_get_req`` down the WS and returns the reply.

        Returns 404 if the node isn't currently connected. Times out
        after 5s with 504 — the WS should reply near-instantly since
        the node just reads its own in-memory config.
        """

        registry: NodeRegistry = app.state.registry
        session = registry.get_session(node_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"node {node_id!r} is not connected")

        req_id = str(uuid.uuid4())
        resp = await _await_config_reply(
            app, session, "node_config_get_req", NodeConfigGetReq(req_id=req_id), req_id
        )
        return {"node_id": node_id, "config": resp.get("config", {})}

    @app.patch(
        "/api/nodes/{node_id}/config",
        tags=["nodes"],
        summary="Apply a partial config update to the node (workspace_root, legacy roots, …).",
    )
    async def patch_node_config(node_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Body: ``{patch: {field: value, …}}`` — only editable fields are honoured.

        Editable: ``workspace_root``, ``legacy_workspace_roots``, ``node_name``,
        ``advertised_url``. Identity (node_id/node_token), pack directory,
        conda plumbing, and gateway URL are read-only from the UI —
        they need config.yaml + a node restart.

        The node validates each field before persisting to config.yaml
        and hot-restarts anything that needs restarting (file server on
        workspace_root change). On invalid input the node returns
        ``ok=False`` with a human-readable ``error``; this endpoint
        surfaces that as a 400 so the frontend can render it inline.
        """

        registry: NodeRegistry = app.state.registry
        session = registry.get_session(node_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"node {node_id!r} is not connected")

        patch = body.get("patch")
        if not isinstance(patch, dict) or not patch:
            raise HTTPException(status_code=400, detail="body must be {patch: {field: value, …}}")

        req_id = str(uuid.uuid4())
        resp = await _await_config_reply(
            app,
            session,
            "node_config_set_req",
            NodeConfigSetReq(req_id=req_id, patch=patch),
            req_id,
        )
        if not resp.get("ok", False):
            raise HTTPException(
                status_code=400,
                detail=resp.get("error") or "node rejected the config patch",
            )
        # Sync the in-memory session view so proxy-URL rewrites (see
        # ``strip_workspace_prefix``) use the freshly-applied roots
        # without needing the node to reconnect. Only mirror fields the
        # NodeSession actually caches — advertised_url etc. flow via the
        # next register frame.
        cfg = resp.get("config", {}) or {}
        new_primary = cfg.get("workspace_root")
        if isinstance(new_primary, str) and new_primary:
            session.workspace_root = new_primary
        new_legacy = cfg.get("legacy_workspace_roots")
        if isinstance(new_legacy, list):
            session.legacy_workspace_roots = [str(p) for p in new_legacy]
        # Same pattern for the Cobrowser device id — mirror straight
        # onto the session so ``GET /api/nodes`` and the frontend's
        # "Open in Cocoder" button pick up the new value without
        # waiting for the node to reconnect.
        if "flops_executor_id" in cfg:
            v = cfg.get("flops_executor_id")
            session.flops_executor_id = v if isinstance(v, str) and v else None
        return {"node_id": node_id, "config": cfg}

    @app.get(
        "/api/artifacts",
        tags=["artifacts"],
        summary="Handle inventory with optional filesystem liveness check.",
    )
    async def list_artifacts(
        workflow_id: str | None = None,
        snapshot_id: str | None = None,
        job_id: str | None = None,
        node_id: str | None = None,
        state: str | None = None,
        check: int = 0,
        limit: int = 500,
    ) -> dict[str, Any]:
        """Rows for the Artifacts page.

        Scopes (mutually exclusive, checked in this order): ``job_id``,
        ``snapshot_id``, ``workflow_id``. Absent all three we return the
        latest ``limit`` handles across every workflow.

        ``check=1`` triggers one batched ``handle_check_req`` per
        producing node, so ``state`` returns real filesystem truth
        (alive/incomplete/dead) instead of the DB-derived
        ``deleted``/``pending`` split.
        """

        from hololab.gateway import artifacts as art

        book: HandleBook = app.state.handles
        registry: NodeRegistry = app.state.registry
        jobs_store: JobsStore = app.state.jobs_store

        if job_id is not None:
            handles = await book.list_by_job(job_id)
        elif snapshot_id is not None:
            handles = await book.list_by_snapshot(snapshot_id)
        elif workflow_id is not None:
            handles = await book.list_by_workflow(workflow_id)
        else:
            handles = await book.list_all(limit=limit)

        if node_id is not None:
            handles = [h for h in handles if h.node_id == node_id]

        job_ids = {h.job_id for h in handles if h.job_id}
        job_meta: dict[str, Any] = {}
        for jid in job_ids:
            j = await jobs_store.get(jid)
            if j is not None:
                job_meta[jid] = j

        drafts = await app.state.workflows.list_drafts()
        wf_names = {d["workflow_id"]: d["name"] for d in drafts}

        handles_by_node: dict[str, list[Any]] = {}
        for h in handles:
            handles_by_node.setdefault(h.node_id, []).append(h)

        live_map: dict[str, dict[str, Any]] = {}
        did_check = bool(check)
        if did_check:
            for nid, node_handles in handles_by_node.items():
                session = registry.get_session(nid)
                if session is None:
                    continue
                node_live = await art.check_handles_live(app, session, node_handles)
                live_map.update(node_live)

        rows: list[art.ArtifactRow] = []
        for h in handles:
            job = job_meta.get(h.job_id or "")
            wf_id = job.workflow_id if job else None
            node_session = registry.get_session(h.node_id)
            if did_check:
                st = art.state_from_check(live_map.get(h.handle_id), h)
            else:
                st = art.derived_state(h)
            rows.append(
                art.format_row(
                    h,
                    node_name=node_session.node_name if node_session else None,
                    workflow_id=wf_id,
                    workflow_name=wf_names.get(wf_id) if wf_id else None,
                    snapshot_id=job.snapshot_id if job else None,
                    algorithm_name=job.algorithm_name if job else None,
                    algorithm_version=job.algorithm_version if job else None,
                    state=st,
                    live_row=live_map.get(h.handle_id),
                )
            )

        if state is not None:
            rows = [r for r in rows if r.state == state]

        counts = art.rollup_states(rows)
        # ``size_bytes`` on the handle row was recorded at register time;
        # for user-cleaned rows we intentionally still count it toward
        # the historical total so run history doesn't look like it
        # produced nothing. Change to filter deleted if that turns out
        # to be more useful in practice.
        total_bytes = sum((r.size_bytes or 0) for r in rows)
        return {
            "rows": [_artifact_row_to_json(r) for r in rows[: max(0, int(limit))]],
            "counts": counts,
            "total_bytes": total_bytes,
            "total_rows": len(rows),
            "checked": did_check,
        }

    @app.delete(
        "/api/artifacts/{handle_id}",
        tags=["artifacts"],
        summary="Delete one artifact from disk + mark the handle deleted.",
    )
    async def delete_one_artifact(handle_id: str) -> dict[str, Any]:
        from hololab.gateway import artifacts as art

        book: HandleBook = app.state.handles
        registry: NodeRegistry = app.state.registry
        handle = await book.get(handle_id)
        if handle is None:
            raise HTTPException(status_code=404, detail="handle not found")
        if handle.deleted_ts is not None:
            # Idempotent — file is already gone by definition.
            return {"handle_id": handle_id, "state": "deleted", "freed_bytes": 0}

        session = registry.get_session(handle.node_id)

        # Producer node offline OR the recorded path lives outside the
        # node's current workspace roots: we can't (and shouldn't) touch
        # the filesystem, but the row is what the Artifacts page wants
        # to hide. Tombstone-only in both cases so history stays
        # coherent and the row falls into the ``deleted`` bucket. This
        # covers the common "old workspace_root was cleaned out
        # externally, we just want to stop showing the dead rows" flow.
        if session is None:
            await book.mark_deleted(handle_id, ts=art.now_ts())
            return {
                "handle_id": handle_id,
                "state": "deleted",
                "freed_bytes": 0,
                "note": "producer node offline — DB row tombstoned; on-disk file untouched",
            }

        try:
            resp = await art.delete_artifact(app, session, handle)
        except HTTPException as exc:
            # ``refused: … is not under any configured workspace root``
            # means the file predates a workspace_root move. The file
            # server can't serve it anyway (see strip_workspace_prefix +
            # legacy roots handling). Tombstone the row and return a
            # note so the caller understands nothing was deleted from
            # disk. Any other node error (permission, IO) still bubbles
            # up as a 5xx.
            detail = str(exc.detail).lower() if exc.detail else ""
            if "not under any configured workspace root" in detail:
                await book.mark_deleted(handle_id, ts=art.now_ts())
                return {
                    "handle_id": handle_id,
                    "state": "deleted",
                    "freed_bytes": 0,
                    "note": (
                        "path outside current workspace roots — DB row tombstoned; "
                        "on-disk file untouched (add a legacy root or delete manually)"
                    ),
                }
            raise

        await book.mark_deleted(handle_id, ts=art.now_ts())
        return {
            "handle_id": handle_id,
            "state": "deleted",
            "freed_bytes": resp.get("freed_bytes"),
        }

    @app.get(
        "/api/artifacts/summary",
        tags=["artifacts"],
        summary="Aggregate byte totals grouped by workflow.",
    )
    async def artifacts_summary() -> dict[str, Any]:
        book: HandleBook = app.state.handles
        rows = await book.list_all(limit=None)
        by_workflow: dict[str, dict[str, Any]] = {}
        drafts = await app.state.workflows.list_drafts()
        wf_names = {d["workflow_id"]: d["name"] for d in drafts}
        # Job → workflow_id lookup, one SELECT per unique job_id.
        job_ids = {h.job_id for h in rows if h.job_id}
        wf_by_job: dict[str, str] = {}
        for jid in job_ids:
            j = await app.state.jobs_store.get(jid)
            if j is not None:
                wf_by_job[jid] = j.workflow_id
        for h in rows:
            wf = wf_by_job.get(h.job_id or "")
            if not wf:
                continue
            entry = by_workflow.setdefault(
                wf,
                {
                    "workflow_id": wf,
                    "workflow_name": wf_names.get(wf),
                    "total_bytes": 0,
                    "count": 0,
                    "deleted_count": 0,
                },
            )
            entry["count"] += 1
            entry["total_bytes"] += h.size_bytes or 0
            if h.deleted_ts is not None:
                entry["deleted_count"] += 1
        return {
            "workflows": list(by_workflow.values()),
            "total_bytes": sum(w["total_bytes"] for w in by_workflow.values()),
            "total_count": sum(w["count"] for w in by_workflow.values()),
        }

    @app.post(
        "/api/artifacts/delete-bulk",
        tags=["artifacts"],
        summary="Delete every handle matching a filter (workflow / snapshot / only-dead).",
    )
    async def bulk_delete_artifacts(body: dict[str, Any]) -> dict[str, Any]:
        from hololab.gateway import artifacts as art

        book: HandleBook = app.state.handles
        registry: NodeRegistry = app.state.registry
        workflow_id = body.get("workflow_id")
        snapshot_id = body.get("snapshot_id")
        only_dead = bool(body.get("only_dead", False))
        if snapshot_id:
            handles = await book.list_by_snapshot(snapshot_id)
        elif workflow_id:
            handles = await book.list_by_workflow(workflow_id)
        else:
            raise HTTPException(status_code=400, detail="require one of workflow_id / snapshot_id")
        handles = [h for h in handles if h.deleted_ts is None]
        if only_dead:
            by_node: dict[str, list[Any]] = {}
            for h in handles:
                by_node.setdefault(h.node_id, []).append(h)
            live_map: dict[str, dict[str, Any]] = {}
            for nid, hs in by_node.items():
                s = registry.get_session(nid)
                if s is None:
                    continue
                live_map.update(await art.check_handles_live(app, s, hs))
            handles = [
                h for h in handles if art.state_from_check(live_map.get(h.handle_id), h) == "dead"
            ]
        results: list[dict[str, Any]] = []
        total_freed = 0
        for h in handles:
            session = registry.get_session(h.node_id)
            try:
                resp = await art.delete_artifact(app, session, h)
                await book.mark_deleted(h.handle_id, ts=art.now_ts())
                total_freed += resp.get("freed_bytes") or 0
                results.append(
                    {
                        "handle_id": h.handle_id,
                        "state": "deleted",
                        "freed_bytes": resp.get("freed_bytes"),
                    }
                )
            except HTTPException as exc:
                results.append({"handle_id": h.handle_id, "state": "error", "error": exc.detail})
        return {
            "requested": len(handles),
            "results": results,
            "freed_bytes_total": total_freed,
        }

    @app.get("/api/packs", tags=["packs"], summary="All packs ever seen (persistent).")
    async def list_packs() -> list[dict[str, Any]]:
        registry: NodeRegistry = app.state.registry
        return await registry.load_persisted_packs_json()

    @app.get(
        "/api/handles/{handle_id}",
        response_model=HandleInfoOut,
        tags=["handles"],
        summary="Resolve a handle_id to its proxy URL + metadata.",
    )
    async def get_handle(handle_id: str) -> dict[str, Any]:
        """Look up one handle by id, returning its metadata + preview proxy URL.

        The frontend hits this after a workflow node reaches ``done`` to
        resolve the output_handle_id it received on the WS ``job_update``
        into a concrete URL it can stream from the preview drawer.

        The proxy sub-path is the handle's absolute path with the producer
        node's ``workspace_root`` stripped, so the URL is stable and
        self-contained even for handles that live several directories
        deep in the workspace.
        """

        book: HandleBook = app.state.handles
        registry: NodeRegistry = app.state.registry

        handle = await book.get(handle_id)
        if handle is None:
            raise HTTPException(status_code=404, detail="handle not found")

        # Compute the preview URL: /proxy/{node_id}/{sub-path}.
        # ``strip_workspace_prefix`` tries the primary root then any legacy
        # roots (see NodeConfig.legacy_workspace_roots), so a handle
        # produced under an older workspace still resolves after a
        # workspace relocation. Falls back to the raw absolute path when
        # none of them match — the URL stays visible for diagnostics.
        session = registry.get_session(handle.node_id)
        sub = strip_workspace_prefix(session, handle.path)
        proxy_url = f"/proxy/{handle.node_id}/{sub.lstrip('/')}"

        return {
            "handle_id": handle.handle_id,
            "node_id": handle.node_id,
            "storage": handle.storage,
            "tags": handle.tags,
            "size_bytes": handle.size_bytes,
            "output_port_name": handle.output_port_name,
            "proxy_url": proxy_url,
            # Producing node's local absolute path — the Cobrowser
            # "Open in Cocoder" button feeds this to
            # ``window.flops.showDocument`` directly. See
            # docs/cobrowser-integration.md.
            "absolute_path": handle.path,
        }

    @app.get(
        "/api/handles/{handle_id}/summary",
        response_model=HandleSummary,
        tags=["handles"],
        summary="Server-parsed metadata for one handle (splatv/video/image/text/dir).",
    )
    async def get_handle_summary_endpoint(handle_id: str) -> dict[str, Any]:
        """Structured payload metadata — no bytes downloaded by the caller.

        Splatv: magic, gaussian count, texture dims, camera count.
        Video: container + size (duration needs ffprobe; not invoked here).
        Image: format + width/height for PNG/JPEG.
        Text: first ~20 lines + line count.
        Dir: entry listing (capped at 100 entries) + total size.
        """

        from hololab.gateway.handle_summary import summarize_handle

        book: HandleBook = app.state.handles
        registry: NodeRegistry = app.state.registry
        handle = await book.get(handle_id)
        if handle is None:
            raise HTTPException(status_code=404, detail="handle not found")

        # Same strip semantics as /api/handles/{id} — primary root then
        # legacy roots so pre-relocation handles keep resolving.
        session = registry.get_session(handle.node_id)
        sub = strip_workspace_prefix(session, handle.path)
        proxy_url = f"/proxy/{handle.node_id}/{sub.lstrip('/')}"

        summary = summarize_handle(handle)
        return {
            "handle_id": handle.handle_id,
            "kind": summary["kind"],
            "tags": handle.tags,
            "storage": handle.storage,
            "size_bytes": handle.size_bytes,
            "proxy_url": proxy_url,
            "absolute_path": handle.path,
            "fields": summary.get("fields", {}),
        }

    @app.get(
        "/api/pack-catalog",
        tags=["packs"],
        summary="Deduped pack catalog with port + param signatures.",
    )
    async def pack_catalog() -> list[dict[str, Any]]:
        """Palette-shaped view: dedup by pack, include port/param signatures."""

        registry: NodeRegistry = app.state.registry
        return registry.catalog_json()

    @app.get(
        "/api/resolve",
        tags=["meta"],
        summary="Resolve a copy-pasted hololab:// reference to its resource + navigation links.",
    )
    async def resolve_ref(ref: str) -> dict[str, Any]:
        """Turn a ``hololab://<kind>/<id>`` token into a full resource + hop links.

        Powers the "user pasted a copy-of-something into chat, tell me
        what it is" agent flow. The UI's ⧉ copy buttons emit the tokens;
        this endpoint is how the agent turns them back into concrete
        resources without any guessing.

        Response shape::

            {
              "kind": "job" | "run" | "workflow" | "handle" | "pack" | "node",
              "ref": "hololab://job/…",
              "resource": { ...the full resource… },
              "related": {"snapshot": "/api/snapshots/…", "log": "/api/jobs/…/log", …}
            }

        ``related`` gives the agent 1-hop navigation links (as URL paths
        relative to this gateway) so it doesn't have to reason about
        endpoint shapes.
        """

        from hololab.gateway.refs import RefParseError, parse_ref

        try:
            parsed = parse_ref(ref)
        except RefParseError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        workflows_store: WorkflowStore = app.state.workflows
        jobs_store: JobsStore = app.state.jobs_store
        book: HandleBook = app.state.handles
        registry: NodeRegistry = app.state.registry

        if parsed.kind == "workflow":
            row = await workflows_store.get_draft(parsed.id)
            if row is None:
                raise HTTPException(status_code=404, detail="workflow not found")
            return {
                "kind": "workflow",
                "ref": parsed.canonical(),
                "resource": {
                    "workflow_id": row.workflow_id,
                    "name": row.name,
                    "graph": agent_graph_dict(row.graph),
                    "created_ts": row.created_ts,
                    "updated_ts": row.updated_ts,
                },
                "related": {
                    "runs": f"/api/workflows/{row.workflow_id}/runs",
                    "run": f"/api/workflows/{row.workflow_id}/run",  # POST to launch
                },
            }

        if parsed.kind == "run":
            snap = await workflows_store.get_snapshot(parsed.id)
            if snap is None:
                raise HTTPException(status_code=404, detail="snapshot not found")
            jobs = await jobs_store.list_by_snapshot(parsed.id)
            return {
                "kind": "run",
                "ref": parsed.canonical(),
                "resource": {
                    "snapshot_id": snap.snapshot_id,
                    "workflow_id": snap.workflow_id,
                    "created_ts": snap.created_ts,
                    "graph": agent_graph_dict(snap.graph),
                    "jobs": jobs,
                },
                "related": {
                    "workflow": f"/api/workflows/{snap.workflow_id}",
                    "runs_list": f"/api/workflows/{snap.workflow_id}/runs",
                    "wait_for_done": (
                        f"/api/snapshots/{snap.snapshot_id}?wait_for_state=done&timeout=1800"
                    ),
                },
            }

        if parsed.kind == "job":
            job = await jobs_store.get(parsed.id)
            if job is None:
                raise HTTPException(status_code=404, detail="job not found")
            related: dict[str, str] = {
                "log": f"/api/jobs/{job.job_id}/log",
                "workflow": f"/api/workflows/{job.workflow_id}",
            }
            if job.snapshot_id:
                related["snapshot"] = f"/api/snapshots/{job.snapshot_id}"
            return {
                "kind": "job",
                "ref": parsed.canonical(),
                "resource": _job_to_json(job),
                "related": related,
            }

        if parsed.kind == "handle":
            handle = await book.get(parsed.id)
            if handle is None:
                raise HTTPException(status_code=404, detail="handle not found")
            # Reuse the proxy_url logic from GET /api/handles/{id}.
            session = registry.get_session(handle.node_id)
            sub = strip_workspace_prefix(session, handle.path)
            proxy_url = f"/proxy/{handle.node_id}/{sub.lstrip('/')}"
            related_handle: dict[str, str] = {
                "summary": f"/api/handles/{handle.handle_id}/summary",
                "bytes": proxy_url,
            }
            if handle.job_id:
                related_handle["job"] = f"/api/jobs/{handle.job_id}"
            return {
                "kind": "handle",
                "ref": parsed.canonical(),
                "resource": {
                    "handle_id": handle.handle_id,
                    "node_id": handle.node_id,
                    "storage": handle.storage,
                    "tags": handle.tags,
                    "size_bytes": handle.size_bytes,
                    "output_port_name": handle.output_port_name,
                    "proxy_url": proxy_url,
                },
                "related": related_handle,
            }

        if parsed.kind == "pack":
            # ident is ``name@version``; find any catalog entry that matches.
            name, _, version = parsed.id.partition("@")
            match = next(
                (
                    entry
                    for entry in registry.catalog_json()
                    if entry["name"] == name and entry["version"] == version
                ),
                None,
            )
            if match is None:
                raise HTTPException(status_code=404, detail="pack not found in current catalog")
            return {
                "kind": "pack",
                "ref": parsed.canonical(),
                "resource": match,
                "related": {"catalog": "/api/pack-catalog"},
            }

        if parsed.kind == "node":
            for row in await registry.as_summary_json():
                if row["node_id"] == parsed.id:
                    return {
                        "kind": "node",
                        "ref": parsed.canonical(),
                        "resource": row,
                        "related": {"nodes": "/api/nodes"},
                    }
            raise HTTPException(status_code=404, detail="node not online")

        if parsed.kind == "workflows":
            # Index-level ref: "this instance's workflow list". An agent
            # who receives this needs to know which workflows exist here
            # so it can pick one to drill into. We return the same shape
            # ``/api/workflows`` renders (used by the Gallery view), just
            # trimmed to the fields useful for triage — id, name,
            # node_count, and a last_run summary — so the payload is
            # small and self-describing.
            rows_full = await list_workflows()  # calls the local endpoint impl below
            summarized = [
                {
                    "workflow_id": w["workflow_id"],
                    "name": w["name"],
                    "node_count": w["node_count"],
                    "updated_ts": w["updated_ts"],
                    "last_run": w.get("last_run"),
                }
                for w in rows_full
            ]
            return {
                "kind": "workflows",
                "ref": parsed.canonical(),
                "resource": {
                    "workflow_count": len(summarized),
                    "workflows": summarized,
                },
                "related": {
                    "list": "/api/workflows",
                    "artifacts_index": "hololab://artifacts",
                },
            }

        if parsed.kind == "artifacts":
            # Index-level ref: "this instance's artifact inventory".
            # Delegate to the same handler that powers the Artifacts
            # page's summary panel (``/api/artifacts/summary``) — same
            # data, one place to keep truthful.
            summary = await artifacts_summary()
            return {
                "kind": "artifacts",
                "ref": parsed.canonical(),
                "resource": summary,
                "related": {
                    "summary": "/api/artifacts/summary",
                    "list": "/api/artifacts",
                    "workflows_index": "hololab://workflows",
                },
            }

        if parsed.kind == "graph-node":
            # A graph node reference is a POSITION in a workflow. It is
            # stable across runs — the position exists as long as the
            # workflow's draft graph mentions that graph_node_id. The
            # response enriches the position with (a) the current draft
            # values (algorithm, params, assignment, edges), and (b) the
            # freshest execution/output at this position via
            # snapshot_jobs, plus a ready-to-POST dispatch URL.
            from hololab.gateway.refs import split_graph_node_id

            workflow_id, graph_node_id = split_graph_node_id(parsed.id)
            draft = await workflows_store.get_draft(workflow_id)
            if draft is None:
                raise HTTPException(status_code=404, detail="workflow not found")
            gnode = next(
                (n for n in draft.graph.nodes if n.id == graph_node_id), None
            )
            if gnode is None:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"graph node {graph_node_id!r} not in workflow "
                        f"{workflow_id!r}'s current draft"
                    ),
                )

            # Compute upstream / downstream edges as a self-describing
            # summary — agents can traverse without re-parsing the graph.
            upstream = [
                {
                    "port": e.targetHandle,
                    "source_graph_node": e.source,
                    "source_port": e.sourceHandle,
                }
                for e in draft.graph.edges
                if e.target == graph_node_id
            ]
            downstream = [
                {
                    "port": e.sourceHandle,
                    "target_graph_node": e.target,
                    "target_port": e.targetHandle,
                }
                for e in draft.graph.edges
                if e.source == graph_node_id
            ]

            # Find the freshest attribution at this graph_node_id across
            # all snapshots for this workflow. Ordering by snapshot
            # created_ts DESC then stopping at the first hit gives us
            # the "most recent artifact at this canvas slot" — matching
            # the same convention the frontend hydration uses.
            latest_job_id: str | None = None
            latest_snapshot_id: str | None = None
            latest_snapshot_created_ts: float | None = None

            async with app.state.db.read() as conn, conn.execute(
                """
                    SELECT sj.job_id, sj.snapshot_id, s.created_ts
                    FROM snapshot_jobs sj
                    JOIN snapshots s ON s.snapshot_id = sj.snapshot_id
                    WHERE s.workflow_id = ?
                      AND sj.graph_node_id = ?
                    ORDER BY s.created_ts DESC
                    LIMIT 1
                    """,
                (workflow_id, graph_node_id),
            ) as cur:
                row = await cur.fetchone()
                if row is not None:
                    latest_job_id = row[0]
                    latest_snapshot_id = row[1]
                    latest_snapshot_created_ts = row[2]

            latest_output_handles: dict[str, str] = {}
            if latest_job_id is not None:
                registered = await book.list_by_job(latest_job_id)
                for h in registered:
                    if h.output_port_name:
                        latest_output_handles[h.output_port_name] = h.handle_id

            resource: dict[str, Any] = {
                "workflow_id": workflow_id,
                "workflow_name": draft.name,
                "graph_node_id": graph_node_id,
                "algorithm_name": gnode.algorithm_name,
                "algorithm_version": gnode.algorithm_version,
                "params": dict(gnode.params),
                "assigned_node_id": gnode.assigned_node_id,
                "upstream": upstream,
                "downstream": downstream,
                "latest_snapshot_id": latest_snapshot_id,
                "latest_snapshot_created_ts": latest_snapshot_created_ts,
                "latest_job_id": latest_job_id,
                "latest_output_handles": latest_output_handles,
            }

            related_gnode: dict[str, str] = {
                "workflow": f"/api/workflows/{workflow_id}",
                "dispatch": (
                    f"/api/workflows/{workflow_id}/dispatch/{graph_node_id}"
                ),  # POST — Continue or Fork onto the latest snapshot
            }
            if latest_snapshot_id is not None:
                related_gnode["latest_run"] = (
                    f"/api/snapshots/{latest_snapshot_id}"
                )
            if latest_job_id is not None:
                related_gnode["latest_job"] = f"/api/jobs/{latest_job_id}"
                related_gnode["latest_job_log"] = (
                    f"/api/jobs/{latest_job_id}/log"
                )

            return {
                "kind": "graph-node",
                "ref": parsed.canonical(),
                "resource": resource,
                "related": related_gnode,
            }

        # Unreachable — parse_ref would have rejected an unknown kind.
        raise HTTPException(status_code=500, detail=f"unhandled kind {parsed.kind!r}")

    # -- workflows -----------------------------------------------------------

    @app.get(
        "/api/workflows",
        response_model=list[WorkflowSummaryOut],
        tags=["workflows"],
        summary="All workflows with node_count + last_run summary (Gallery + agent).",
    )
    async def list_workflows() -> list[dict[str, Any]]:
        """Gallery-shaped list of every workflow.

        Rows come out of ``list_drafts`` with ``node_count`` already
        computed; we then attach a ``last_run`` summary (most recent
        snapshot + its job state rollup) per workflow so the Gallery
        cards can render in one round-trip. Workflows with no runs get
        ``last_run: null`` so the card can show the empty-state pip
        instead of a wrong "done" colour.
        """

        workflows_store: WorkflowStore = app.state.workflows
        jobs_store: JobsStore = app.state.jobs_store

        drafts = await workflows_store.list_drafts()

        # N+1 queries by design — the Gallery loads ~dozens of rows, and
        # a JOIN across snapshots+jobs+workflows with a per-workflow
        # LATERAL is way more code to write and maintain than two
        # awaited calls per row. Revisit if the drafts list ever grows
        # into the thousands.
        for row in drafts:
            snapshots = await workflows_store.list_snapshots_for_workflow(row["workflow_id"])
            if not snapshots:
                row["last_run"] = None
                continue
            latest = snapshots[0]  # newest first
            jobs = await jobs_store.list_by_snapshot(latest.snapshot_id)
            state_counts: dict[str, int] = {}
            for j in jobs:
                state_counts[j["state"]] = state_counts.get(j["state"], 0) + 1
            # Same rollup rules the runs endpoint uses so the Gallery
            # pip and the Runs panel pip agree at a glance.
            if not jobs:
                rollup = "pending"
            elif state_counts.get("failed", 0) > 0:
                rollup = "failed"
            elif state_counts.get("cancelled", 0) > 0 and state_counts.get("running", 0) == 0:
                rollup = "cancelled"
            elif all(j["state"] == "done" for j in jobs):
                rollup = "done"
            elif any(j["state"] in ("running", "assigned") for j in jobs):
                rollup = "running"
            else:
                rollup = "pending"
            row["last_run"] = {
                "snapshot_id": latest.snapshot_id,
                "created_ts": latest.created_ts,
                "state": rollup,
                "state_counts": state_counts,
                "job_count": len(jobs),
            }
        return drafts

    @app.get(
        "/api/workflows/{workflow_id}",
        response_model=WorkflowDetailOut,
        tags=["workflows"],
        summary="One workflow draft with agent-shaped graph (topological order + labels + topology_text).",
    )
    async def get_workflow(workflow_id: str) -> dict[str, Any]:
        row = await app.state.workflows.get_draft(workflow_id)
        if row is None:
            raise HTTPException(status_code=404, detail="workflow not found")
        return {
            "workflow_id": row.workflow_id,
            "name": row.name,
            "graph": agent_graph_dict(row.graph),
            "created_ts": row.created_ts,
            "updated_ts": row.updated_ts,
        }

    @app.post(
        "/api/workflows",
        response_model=WorkflowSaveResult,
        tags=["workflows"],
        summary="Create or update a workflow draft.",
    )
    async def save_workflow(body: dict[str, Any]) -> dict[str, Any]:
        """Upsert a draft. Body: ``{workflow_id?, name, graph}``."""

        try:
            name = body["name"]
            graph_raw = body["graph"]
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=f"missing field: {exc}") from exc
        try:
            graph = WorkflowGraph.model_validate(graph_raw)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"invalid graph: {exc}") from exc

        row = await app.state.workflows.save_draft(
            workflow_id=body.get("workflow_id"), name=name, graph=graph
        )
        return {
            "workflow_id": row.workflow_id,
            "name": row.name,
            "updated_ts": row.updated_ts,
        }

    @app.delete(
        "/api/workflows/{workflow_id}",
        response_model=WorkflowDeleteResult,
        tags=["workflows"],
        summary="Delete a workflow draft (past snapshots + jobs are preserved).",
    )
    async def delete_workflow(workflow_id: str) -> dict[str, str]:
        await app.state.workflows.delete_draft(workflow_id)
        return {"workflow_id": workflow_id, "state": "deleted"}

    @app.post(
        "/api/workflows/{workflow_id}/run",
        response_model=WorkflowRunResult,
        tags=["runs"],
        summary="Snapshot the current draft and start execution in the background.",
    )
    async def run_workflow(workflow_id: str) -> dict[str, Any]:
        """Snapshot the current draft, validate, then execute in the background.

        Returns immediately with the snapshot id + list of jobs the executor
        will create. Job progress flows to subscribed frontends via the
        existing ``job_update`` broadcast.
        """

        row = await app.state.workflows.get_draft(workflow_id)
        if row is None:
            raise HTTPException(status_code=404, detail="workflow not found")

        registry: NodeRegistry = app.state.registry

        # Build validation inputs from the catalog + connected sessions.
        catalog = {(c["name"], c["version"]): c for c in registry.catalog_json()}
        packs_by_key: dict[tuple[str, str], PackHandle] = {
            key: PackHandle(
                inputs={
                    n: (list(i["tags"]), bool(i.get("required", True)))
                    for n, i in entry["inputs"].items()
                },
                outputs={n: (list(o["tags"]),) for n, o in entry["outputs"].items()},
            )
            for key, entry in catalog.items()
        }
        online_node_ids = {s.node_id for s in registry.all_sessions()}
        packs_offered_by_node: dict[str, set[tuple[str, str]]] = {}
        for session in registry.all_sessions():
            packs_offered_by_node[session.node_id] = {(p.name, p.version) for p in session.packs}

        issues = validate_snapshot(
            row.graph,
            packs_by_key=packs_by_key,
            online_node_ids=online_node_ids,
            packs_offered_by_node=packs_offered_by_node,
        )
        if issues:
            raise HTTPException(status_code=422, detail={"issues": issues_to_json(issues)})

        snapshot = await app.state.workflows.create_snapshot(
            workflow_id=workflow_id, graph=row.graph
        )

        async def _run() -> None:
            try:
                await run_snapshot(
                    app,
                    snapshot_id=snapshot.snapshot_id,
                    graph=snapshot.graph,
                    workflow_id=workflow_id,
                )
            except WorkflowRunError as exc:
                log.warning(
                    "workflow run aborted", snapshot_id=snapshot.snapshot_id, error=str(exc)
                )

        # We deliberately don't await this — the REST call returns while the
        # DAG is still executing in the background; job_update broadcasts drive
        # the UI. The task is retained on app.state so the GC doesn't collect it.
        run_task = asyncio.create_task(_run(), name=f"hololab-run-{snapshot.snapshot_id}")
        _run_tasks = getattr(app.state, "workflow_run_tasks", set())
        _run_tasks.add(run_task)
        run_task.add_done_callback(_run_tasks.discard)
        app.state.workflow_run_tasks = _run_tasks

        return {
            "workflow_id": workflow_id,
            "snapshot_id": snapshot.snapshot_id,
            "node_count": len(snapshot.graph.nodes),
        }

    @app.get(
        "/api/workflows/{workflow_id}/runs",
        response_model=list[RunSummary],
        tags=["runs"],
        summary="Every historical run of one workflow, newest first, with state rollup.",
    )
    async def list_workflow_runs(workflow_id: str) -> list[dict[str, Any]]:
        """Every historical run of one workflow, newest first.

        A "run" is one snapshot + all its jobs. We aggregate job states
        into a compact ``state_counts`` map (``done``/``failed``/``running``/
        …) so the frontend can render a one-glance status without a
        second round-trip per row. The full job list lives at
        ``/api/snapshots/{snapshot_id}``.
        """

        store: JobsStore = app.state.jobs_store
        workflows_store: WorkflowStore = app.state.workflows
        handles: HandleBook = app.state.handles

        # 404 rather than empty list on unknown workflow so the UI can
        # distinguish "no runs yet" from "no such workflow".
        draft = await workflows_store.get_draft(workflow_id)
        if draft is None:
            raise HTTPException(status_code=404, detail="workflow not found")

        snapshots = await workflows_store.list_snapshots_for_workflow(workflow_id)
        out: list[dict[str, Any]] = []
        for snap in snapshots:
            jobs = await store.list_by_snapshot(snap.snapshot_id)
            state_counts: dict[str, int] = {}
            for j in jobs:
                state_counts[j["state"]] = state_counts.get(j["state"], 0) + 1
            # A run is "done" iff every job in it is done; "failed" if any
            # is failed; else "running" (something's still moving) or
            # "pending" if nothing has moved yet. This mirrors the badge
            # colour heuristic the frontend already uses per-node.
            if not jobs:
                rollup = "pending"
            elif state_counts.get("failed", 0) > 0:
                rollup = "failed"
            elif state_counts.get("cancelled", 0) > 0 and state_counts.get("running", 0) == 0:
                rollup = "cancelled"
            elif all(j["state"] == "done" for j in jobs):
                rollup = "done"
            elif any(j["state"] in ("running", "assigned") for j in jobs):
                rollup = "running"
            else:
                rollup = "pending"
            # Cheap artifact rollup — a DB-only count so this endpoint
            # stays snappy. The Artifacts page opts into the real
            # filesystem check via ``?check=1`` on /api/artifacts.
            artifact_counts = await handles.count_by_snapshot(snap.snapshot_id)
            out.append(
                {
                    "snapshot_id": snap.snapshot_id,
                    "workflow_id": snap.workflow_id,
                    "created_ts": snap.created_ts,
                    "node_count": len(snap.graph.nodes),
                    "job_count": len(jobs),
                    "state_counts": state_counts,
                    "state": rollup,
                    "artifact_counts": artifact_counts,
                }
            )
        return out

    @app.get(
        "/api/snapshots/{snapshot_id}",
        response_model=SnapshotDetailOut,
        tags=["runs"],
        summary="One run's frozen graph + jobs; optional long-poll on ``?wait_for_state=``.",
    )
    async def get_snapshot(
        snapshot_id: str,
        wait_for_state: str | None = None,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        """One run's full detail.

        Without ``wait_for_state`` this is a simple point-in-time read.

        With ``wait_for_state`` the server long-polls up to ``timeout``
        seconds (capped at 1800) waiting for the run's rollup to reach
        the requested state. Values:

        * ``done``   — every job terminal in state ``done``;
        * ``failed`` — at least one job in a failure state;
        * ``any``    — every job in some terminal state.

        Returns the current snapshot detail as soon as the condition is
        met, or the last observed detail with ``wait_timed_out=True`` if
        the timeout elapses. Designed for agent workflows: one call
        ``GET /api/snapshots/{id}?wait_for_state=done&timeout=1800``
        replaces a client-side polling loop.
        """

        store: JobsStore = app.state.jobs_store
        snap = await app.state.workflows.get_snapshot(snapshot_id)
        if snap is None:
            raise HTTPException(status_code=404, detail="snapshot not found")

        _WAIT_CAP_SECONDS = 1800.0
        capped_timeout = max(0.0, min(timeout, _WAIT_CAP_SECONDS))

        # Total nodes in the frozen graph — required to distinguish
        # "one job done so far" from "the whole DAG is done", because
        # the executor dispatches jobs one at a time in topological
        # order (see execution.run_snapshot). Without this the wait
        # would return prematurely after the first node.
        total_nodes = len(snap.graph.nodes)

        def _matches(jobs: list[dict[str, Any]], target: str) -> bool:
            terminal = {"done", "failed", "cancelled", "orphaned"}
            if target == "done":
                # All nodes must have dispatched AND all must be done.
                return len(jobs) >= total_nodes and all(j["state"] == "done" for j in jobs)
            if target == "failed":
                # Any failure short-circuits the DAG, so the executor
                # will stop dispatching. Match as soon as ANY job is
                # failed — no need to wait for the full count.
                return any(j["state"] == "failed" for j in jobs)
            if target == "any":
                # "Any terminal state" means the run is over one way or
                # another — again, only meaningful once we've seen all
                # nodes OR at least one failure/cancel stopped the DAG.
                if any(j["state"] in ("failed", "cancelled") for j in jobs):
                    return True
                return len(jobs) >= total_nodes and all(j["state"] in terminal for j in jobs)
            raise HTTPException(
                status_code=400,
                detail=f"wait_for_state must be done|failed|any, got {target!r}",
            )

        waited = False
        wait_timed_out = False
        jobs = await store.list_by_snapshot(snapshot_id)
        if wait_for_state is not None:
            waited = True
            deadline = time.time() + capped_timeout
            poll_interval = 0.5
            while not _matches(jobs, wait_for_state):
                if time.time() >= deadline:
                    wait_timed_out = True
                    break
                await asyncio.sleep(poll_interval)
                # Backoff up to 2s so a 20-min run doesn't hammer the DB.
                poll_interval = min(poll_interval * 1.5, 2.0)
                jobs = await store.list_by_snapshot(snapshot_id)

        return {
            "snapshot_id": snap.snapshot_id,
            "workflow_id": snap.workflow_id,
            "created_ts": snap.created_ts,
            "graph": agent_graph_dict(snap.graph),
            "jobs": jobs,
            "waited": waited,
            "wait_timed_out": wait_timed_out,
        }

    @app.post(
        "/api/workflows/{workflow_id}/restore-from-snapshot/{snapshot_id}",
        response_model=RestoreResult,
        tags=["runs"],
        summary="Overwrite the draft with a past run's frozen graph (clone params back).",
    )
    async def restore_from_snapshot(workflow_id: str, snapshot_id: str) -> dict[str, Any]:
        """Overwrite the draft with a past run's frozen graph.

        Effectively a "clone parameters back" affordance for the classic
        research loop: run → look at result → tweak one param → run
        again. Without this the user has to eyeball ``params_json`` from
        the jobs table and hand-edit the draft.

        The snapshot must belong to the same workflow — cross-workflow
        restore is rejected so you can't accidentally overwrite draft A
        with a run from draft B.
        """

        snap = await app.state.workflows.get_snapshot(snapshot_id)
        if snap is None:
            raise HTTPException(status_code=404, detail="snapshot not found")
        if snap.workflow_id != workflow_id:
            raise HTTPException(
                status_code=400,
                detail="snapshot belongs to a different workflow",
            )
        draft = await app.state.workflows.get_draft(workflow_id)
        if draft is None:
            raise HTTPException(status_code=404, detail="workflow not found")

        row = await app.state.workflows.save_draft(
            workflow_id=workflow_id, name=draft.name, graph=snap.graph
        )
        return {
            "workflow_id": row.workflow_id,
            "name": row.name,
            "restored_from_snapshot_id": snapshot_id,
            "updated_ts": row.updated_ts,
        }

    @app.post(
        "/api/snapshots/{snapshot_id}/rerun-from/{graph_node_id}",
        tags=["runs"],
        summary="Re-run a snapshot starting from one node; upstream outputs are reused.",
    )
    async def rerun_from_node(snapshot_id: str, graph_node_id: str) -> dict[str, Any]:
        """Create a new snapshot that reuses upstream outputs from the given
        run and re-executes ``graph_node_id`` + everything downstream.

        Semantics:
          * ``to_rerun`` = {graph_node_id} plus its transitive downstream
            in the old snapshot's graph.
          * ``to_reuse`` = every other node in the graph. Each must have
            a ``done`` job in the old snapshot; the producer compute
            node must currently be online so the frontend can still
            stream that handle's bytes through ``/proxy``.
          * A new snapshot is created (same frozen graph, new
            snapshot_id). Reused nodes get done Job rows with
            ``reused_from_job_id`` set to the old job. Re-run nodes are
            dispatched normally in the background.

        Errors:
          * 404 — snapshot not found, or graph_node_id not in graph.
          * 400 — a to_reuse node has no done job in the old run.
          * 400 — the producer compute node for a reused output is offline.
        """

        from hololab.gateway.workflows import downstream_closure

        registry: NodeRegistry = app.state.registry
        jobs_store: JobsStore = app.state.jobs_store
        handles: HandleBook = app.state.handles
        workflows_store: WorkflowStore = app.state.workflows

        old_snap = await workflows_store.get_snapshot(snapshot_id)
        if old_snap is None:
            raise HTTPException(status_code=404, detail="snapshot not found")

        node_ids = {n.id for n in old_snap.graph.nodes}
        if graph_node_id not in node_ids:
            raise HTTPException(
                status_code=404,
                detail=f"graph node {graph_node_id!r} not in this snapshot's graph",
            )

        to_rerun = downstream_closure(old_snap.graph, graph_node_id)
        to_reuse = node_ids - to_rerun

        # Old-snapshot jobs indexed by graph_node_id for the reuse plan.
        old_jobs = await jobs_store.list_by_snapshot(snapshot_id)
        old_jobs_by_gnode: dict[str, dict[str, Any]] = {}
        for j in old_jobs:
            gid = j.get("graph_node_id")
            # Keep the newest job per graph_node — belt-and-braces since the
            # executor only creates one job per (snapshot, graph_node), but
            # rerun-from could conceivably layer more.
            if gid and (
                gid not in old_jobs_by_gnode
                or j["created_ts"] > old_jobs_by_gnode[gid]["created_ts"]
            ):
                old_jobs_by_gnode[gid] = j

        # Validate every to_reuse node has a done job with resolvable
        # output handles from an online producer.
        reused_pre_completed: dict[str, dict[str, str]] = {}
        reused_from_by_graph_node: dict[str, str] = {}
        online_node_ids = {s.node_id for s in registry.all_sessions()}
        for gid in to_reuse:
            old = old_jobs_by_gnode.get(gid)
            if old is None or old["state"] != "done":
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"cannot reuse graph node {gid!r}: it has no done job in "
                        f"the source run (state was "
                        f"{old['state'] if old else 'never dispatched'})"
                    ),
                )
            # The producer node must be online so /proxy can serve the handle.
            producer_node = old.get("node_id")
            if producer_node and producer_node not in online_node_ids:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"cannot reuse graph node {gid!r}: its producer compute "
                        f"node {producer_node!r} is not currently online"
                    ),
                )
            # ``old`` may itself be a reused row from an earlier rerun-from,
            # in which case its output handles live under the origin job's
            # ID — walking ``reused_from_job_id`` finds the real producer.
            # Without this, "rerun a snapshot that was itself the product
            # of a rerun" aborts with
            # ``upstream job produced no output for port 'X'``.
            origin_job_id = await _resolve_origin_job_id(jobs_store, old)
            registered = await handles.list_by_job(origin_job_id)
            by_port = {h.output_port_name: h.handle_id for h in registered if h.output_port_name}
            reused_pre_completed[gid] = by_port
            # Store the flattened origin so the new reused row's
            # ``reused_from_job_id`` also points at the origin — chains
            # stay one-hop deep no matter how many reruns pile up.
            reused_from_by_graph_node[gid] = origin_job_id

        # Confirm every to_rerun node is assigned to an online compute node
        # (validate_snapshot would catch this on a fresh /run, but we're
        # bypassing that path — do it explicitly).
        for gnode in old_snap.graph.nodes:
            if gnode.id not in to_rerun:
                continue
            if gnode.assigned_node_id is None or gnode.assigned_node_id not in online_node_ids:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"cannot re-run graph node {gnode.id!r}: assigned compute "
                        f"node {gnode.assigned_node_id!r} is not online"
                    ),
                )

        new_snapshot = await workflows_store.create_snapshot(
            workflow_id=old_snap.workflow_id,
            graph=old_snap.graph,
            parent_snapshot_id=snapshot_id,
        )

        # V8 attribution: bridge every reused origin job into the new
        # snapshot at its graph_node_id. This replaces the pre-V8
        # "synthetic reused job row" bookkeeping — no fake Job records
        # are created; snapshot_jobs alone captures the sharing. The
        # frontend's ``list_by_snapshot`` join then sees the *original*
        # jobs verbatim (real params, real timestamps).
        snapshot_jobs_store: SnapshotJobsStore = app.state.snapshot_jobs
        inherited = [(reused_from_by_graph_node[gid], gid) for gid in to_reuse]
        await snapshot_jobs_store.attribute_many(new_snapshot.snapshot_id, inherited)

        async def _run() -> None:
            try:
                await run_snapshot(
                    app,
                    snapshot_id=new_snapshot.snapshot_id,
                    graph=old_snap.graph,
                    workflow_id=old_snap.workflow_id,
                    skip_graph_nodes=to_reuse,
                    seed_outputs=reused_pre_completed,
                )
            except WorkflowRunError as exc:
                log.warning(
                    "rerun-from aborted", snapshot_id=new_snapshot.snapshot_id, error=str(exc)
                )

        run_task = asyncio.create_task(_run(), name=f"hololab-rerun-{new_snapshot.snapshot_id}")
        _run_tasks = getattr(app.state, "workflow_run_tasks", set())
        _run_tasks.add(run_task)
        run_task.add_done_callback(_run_tasks.discard)
        app.state.workflow_run_tasks = _run_tasks

        return {
            "workflow_id": old_snap.workflow_id,
            "original_snapshot_id": snapshot_id,
            "new_snapshot_id": new_snapshot.snapshot_id,
            "rerun_from_graph_node_id": graph_node_id,
            "rerun_graph_node_ids": sorted(to_rerun),
            "reused_graph_node_ids": sorted(to_reuse),
            "reused_job_ids": [reused_from_by_graph_node[gid] for gid in sorted(to_reuse)],
        }

    @app.post(
        "/api/workflows/{workflow_id}/dispatch/{graph_node_id}",
        tags=["runs"],
        summary=(
            "Continue-or-Fork dispatch of a single graph node onto the "
            "workflow's most recent snapshot (V8 model)."
        ),
    )
    async def dispatch_single_node(
        workflow_id: str,
        graph_node_id: str,
        base_snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        """Dispatch one graph node (Continue or Fork on a base snapshot).

        Semantics (see ``docs/workflow-schema.md#lineage-snapshots``):
          * Continue — the target graph node has NO produced artifact in
            the base snapshot yet. The new job is attributed to the base
            snapshot; snapshot_id doesn't change. Every upstream input
            must already be produced in the base snapshot (the caller
            has to run upstream first).
          * Fork — the target graph node already has a produced artifact
            in the base snapshot. A new snapshot is created with the
            base as ``parent_snapshot_id``; every parent-snapshot
            attribution at nodes NOT downstream of the fork point is
            inherited into the new snapshot; the new job runs at the
            fork point.
          * If ``base_snapshot_id`` is None the caller wants a fresh
            snapshot — Continue on empty; the workflow's history gets
            one new head.

        Response shape::

            {
              "snapshot_id":        "<target snapshot>",
              "job_id":             "<newly dispatched job>",
              "operation":          "continue" | "fork",
              "parent_snapshot_id": "<only when operation='fork'>",
              "forked_at":          "<only when operation='fork'>"
            }

        Errors:
          * 404 — workflow not found; graph node not in the current draft.
          * 400 — upstream inputs not resolvable in the base snapshot
            (dispatch upstream first); assigned compute node offline.
        """

        workflows_store: WorkflowStore = app.state.workflows
        row = await workflows_store.get_draft(workflow_id)
        if row is None:
            raise HTTPException(status_code=404, detail="workflow not found")

        # If the caller didn't name a base, default to the workflow's
        # most recent snapshot (Continue/Fork extends the head). None
        # is fine — it just means "start a fresh snapshot".
        if base_snapshot_id is None:
            snapshot_jobs_store: SnapshotJobsStore = app.state.snapshot_jobs
            base_snapshot_id = await snapshot_jobs_store.get_latest_snapshot_for_workflow(
                workflow_id
            )

        try:
            result = await dispatch_graph_node(
                app,
                workflow_id=workflow_id,
                graph=row.graph,
                graph_node_id=graph_node_id,
                base_snapshot_id=base_snapshot_id,
            )
        except DispatchError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return result

    # -- cosmetic patch endpoints ---------------------------------------------
    #
    # ``preview_open`` and ``position`` are cosmetic — they don't
    # participate in dispatch, so mutating them doesn't Fork a
    # snapshot. Two endpoints, one for each editable graph_json:
    #
    #   * Draft edit → also mirrored onto the workflow's most recent
    #     snapshot so the "back to last run" hydration stays in sync.
    #   * Snapshot edit → only that snapshot is touched.
    #
    # The server enforces the cosmetic allowlist regardless of what the
    # body contains — a client that tries to smuggle a ``params`` edit
    # through the cosmetic endpoint gets 400.

    _COSMETIC_FIELDS: set[str] = {"preview_open", "position"}

    def _validate_cosmetic_patch(patch: dict[str, Any]) -> dict[str, Any]:
        """Return the subset of ``patch`` in the cosmetic allowlist.

        Rejects (400) if any field outside the allowlist is present.
        Empty patches are also rejected — a no-op call is a bug on the
        caller's side, not a legal request.
        """

        stray = set(patch.keys()) - _COSMETIC_FIELDS
        if stray:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"cosmetic patch may only touch {sorted(_COSMETIC_FIELDS)}; "
                    f"non-cosmetic fields present: {sorted(stray)}"
                ),
            )
        if not patch:
            raise HTTPException(status_code=400, detail="cosmetic patch is empty")
        return patch

    def _apply_cosmetic_to_graph_json(
        graph_json: str, graph_node_id: str, patch: dict[str, Any]
    ) -> str:
        """Patch one graph node's cosmetic fields inside a stored JSON blob.

        We parse → mutate → re-serialise rather than round-tripping
        through :class:`WorkflowGraph` because the stored blob may
        legitimately contain historical fields the current schema no
        longer knows about (or vice versa — new field on old row).
        Direct JSON manipulation is the only way to keep unrelated
        fields intact across schema evolution.
        """

        data = json.loads(graph_json)
        nodes = data.get("nodes", [])
        target = None
        for n in nodes:
            if n.get("id") == graph_node_id:
                target = n
                break
        if target is None:
            raise HTTPException(
                status_code=404,
                detail=f"graph node {graph_node_id!r} not found in graph",
            )
        for key, value in patch.items():
            target[key] = value
        return json.dumps(data)

    @app.patch(
        "/api/workflows/{workflow_id}/graph-nodes/{graph_node_id}/cosmetic",
        tags=["workflows"],
        summary=(
            "Update cosmetic fields (preview_open / position) on one graph node."
        ),
    )
    async def patch_workflow_graph_node_cosmetic(
        workflow_id: str,
        graph_node_id: str,
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        """Update a graph node's cosmetic fields on the draft AND mirror
        the change to the workflow's most recent snapshot.

        The "current corresponding snapshot" from the V8 model — the one
        the draft view hydrates state badges + preview carets from — is
        the workflow's newest snapshot. Mirroring cosmetic edits there
        means the "switch to the last run view" experience carries the
        drawer state over instead of resetting it, which is what the
        cosmetic-vs-structural boundary is *for*.

        Response includes both the draft's post-patch node view and
        whether the latest snapshot was mirrored (``mirrored_to``:
        snapshot_id or ``null`` when the workflow has no runs yet).
        """

        validated = _validate_cosmetic_patch(patch)
        workflows_store: WorkflowStore = app.state.workflows

        # ``Database.write`` already serialises through the writer queue,
        # so consecutive writes below are ordered without an explicit
        # lock; readers use the WAL reader connection.
        async with app.state.db.read() as conn, conn.execute(
            "SELECT draft_json FROM workflows WHERE workflow_id=?",
            (workflow_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="workflow not found")
        new_draft_json = _apply_cosmetic_to_graph_json(
            row[0], graph_node_id, validated
        )

        async def _write_draft(conn: aiosqlite.Connection) -> None:
            await conn.execute(
                "UPDATE workflows SET draft_json=?, updated_ts=? "
                "WHERE workflow_id=?",
                (new_draft_json, time.time(), workflow_id),
            )

        await app.state.db.write(_write_draft)

        # Mirror to the latest snapshot (if the workflow has any).
        snapshots = await workflows_store.list_snapshots_for_workflow(
            workflow_id
        )
        mirrored_to: str | None = None
        if snapshots:
            latest_snapshot = snapshots[0]
            async with app.state.db.read() as conn, conn.execute(
                "SELECT graph_json FROM snapshots WHERE snapshot_id=?",
                (latest_snapshot.snapshot_id,),
            ) as cur:
                srow = await cur.fetchone()
            if srow is not None:
                try:
                    new_snap_json = _apply_cosmetic_to_graph_json(
                        srow[0], graph_node_id, validated
                    )
                except HTTPException:
                    # Snapshot's frozen graph may not contain this
                    # graph_node_id (draft was edited to add nodes
                    # after the run). Mirror is best-effort — the
                    # draft update above already succeeded.
                    new_snap_json = None
                if new_snap_json is not None:
                    async def _write_snap(conn: aiosqlite.Connection) -> None:
                        await conn.execute(
                            "UPDATE snapshots SET graph_json=? "
                            "WHERE snapshot_id=?",
                            (new_snap_json, latest_snapshot.snapshot_id),
                        )

                    await app.state.db.write(_write_snap)
                    mirrored_to = latest_snapshot.snapshot_id

        return {
            "workflow_id": workflow_id,
            "graph_node_id": graph_node_id,
            "applied": validated,
            "mirrored_to": mirrored_to,
        }

    @app.patch(
        "/api/snapshots/{snapshot_id}/graph-nodes/{graph_node_id}/cosmetic",
        tags=["runs"],
        summary=(
            "Update cosmetic fields on one snapshot's graph node (structural "
            "fields remain immutable)."
        ),
    )
    async def patch_snapshot_graph_node_cosmetic(
        snapshot_id: str,
        graph_node_id: str,
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        """Update a snapshot's own graph_json for cosmetic fields.

        Historical snapshots each carry their own cosmetic state — the
        drawer users had open then vs. now can legitimately differ.
        This endpoint lets the read-only snapshot canvas persist a
        drawer toggle without propagating elsewhere.
        """

        validated = _validate_cosmetic_patch(patch)

        async with app.state.db.read() as conn, conn.execute(
            "SELECT graph_json FROM snapshots WHERE snapshot_id=?",
            (snapshot_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="snapshot not found")
        new_json = _apply_cosmetic_to_graph_json(
            row[0], graph_node_id, validated
        )

        async def _write(conn: aiosqlite.Connection) -> None:
            await conn.execute(
                "UPDATE snapshots SET graph_json=? WHERE snapshot_id=?",
                (new_json, snapshot_id),
            )

        await app.state.db.write(_write)

        return {
            "snapshot_id": snapshot_id,
            "graph_node_id": graph_node_id,
            "applied": validated,
        }

    @app.get(
        "/api/artifacts/{handle_id}/lineage",
        tags=["artifacts"],
        summary=(
            "Provenance DAG for one artifact — walks ancestors and descendants "
            "through jobs.input_handles."
        ),
    )
    async def get_artifact_lineage(
        handle_id: str,
        direction: str = "both",
        max_depth: int = 20,
    ) -> dict[str, Any]:
        """Return the provenance sub-DAG of one artifact.

        Blood lineage is derivable from what we already store: each
        artifact carries a ``job_id`` (its producer); each job carries
        an ``input_handles`` map (its consumed artifacts). Walking this
        pair gives us the full ancestor / descendant graph without
        introducing a redundant column. See docs about the artifact
        provenance graph — same shape as the workflow canvas, one node
        per artifact.

        Args:
            handle_id: the artifact to root the lineage at.
            direction: ``"up"`` (ancestors only), ``"down"`` (descendants
                only), ``"both"`` (default).
            max_depth: bounded BFS to prevent traversing degenerate
                DAGs. Clamp between 1 and 200.

        Response shape::

            {
              "root":     "<handle_id>",
              "nodes":    [{artifact_id, producing_job_id, algorithm,
                             version, params, node_id, storage,
                             created_ts}],
              "edges":    [{parent: artifact_id, child: artifact_id,
                             consuming_port_name, consuming_job_id}]
            }
        """

        if direction not in ("up", "down", "both"):
            raise HTTPException(status_code=400, detail="direction must be up|down|both")
        max_depth = max(1, min(max_depth, 200))

        book: HandleBook = app.state.handles
        jobs_store: JobsStore = app.state.jobs_store

        root = await book.get(handle_id)
        if root is None:
            raise HTTPException(status_code=404, detail="artifact not found")

        seen_handles: set[str] = {handle_id}
        seen_edges: set[tuple[str, str, str]] = set()
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []

        async def _add_node(h: Handle) -> None:
            producer = await jobs_store.get(h.job_id) if h.job_id else None
            nodes.append(
                {
                    "artifact_id": h.handle_id,
                    "producing_job_id": h.job_id,
                    "algorithm": producer.algorithm_name if producer else None,
                    "version": producer.algorithm_version if producer else None,
                    "params": producer.params if producer else {},
                    "node_id": h.node_id,
                    "storage": h.storage,
                    "output_port_name": h.output_port_name,
                    "size_bytes": h.size_bytes,
                    "created_ts": h.created_ts,
                    "deleted_ts": h.deleted_ts,
                }
            )

        await _add_node(root)

        # ----- Ancestor walk (up) -----
        # For each handle H, find its producing job J. J.input_handles
        # is the set of ancestor handles at one hop up. Recurse to
        # ``max_depth`` levels.
        if direction in ("up", "both"):
            frontier: list[Handle] = [root]
            for _ in range(max_depth):
                next_frontier: list[Handle] = []
                for h in frontier:
                    if h.job_id is None:
                        continue
                    producer = await jobs_store.get(h.job_id)
                    if producer is None:
                        continue
                    for port_name, parent_hid in (producer.input_handles or {}).items():
                        edge_key = (parent_hid, h.handle_id, port_name)
                        if edge_key in seen_edges:
                            continue
                        seen_edges.add(edge_key)
                        edges.append(
                            {
                                "parent": parent_hid,
                                "child": h.handle_id,
                                "consuming_port_name": port_name,
                                "consuming_job_id": producer.job_id,
                            }
                        )
                        if parent_hid in seen_handles:
                            continue
                        seen_handles.add(parent_hid)
                        parent_handle = await book.get(parent_hid)
                        if parent_handle is None:
                            continue
                        await _add_node(parent_handle)
                        next_frontier.append(parent_handle)
                if not next_frontier:
                    break
                frontier = next_frontier

        # ----- Descendant walk (down) -----
        # Find every job whose input_handles map contains the given
        # handle; each such job's produced handles are descendants at
        # one hop down. We use the input_handles JSON via LIKE — a full
        # scan on the ``jobs`` table because we have no side index over
        # input_handles_json content. For our fleet size (thousands of
        # jobs at most) that's fine; for larger fleets a dedicated
        # input-edges table would justify itself.
        if direction in ("down", "both"):
            frontier_ids: list[str] = [handle_id]
            for _ in range(max_depth):
                next_frontier_ids: list[str] = []
                for parent_hid in frontier_ids:
                    consumers = await _list_consumer_jobs(jobs_store, parent_hid)
                    for consumer in consumers:
                        # Which port did the consumer use for this parent?
                        consumed_port = next(
                            (
                                p
                                for p, hid in (consumer.input_handles or {}).items()
                                if hid == parent_hid
                            ),
                            None,
                        )
                        produced_handles = await book.list_by_job(consumer.job_id)
                        for produced in produced_handles:
                            edge_key = (parent_hid, produced.handle_id, consumed_port or "")
                            if edge_key in seen_edges:
                                continue
                            seen_edges.add(edge_key)
                            edges.append(
                                {
                                    "parent": parent_hid,
                                    "child": produced.handle_id,
                                    "consuming_port_name": consumed_port,
                                    "consuming_job_id": consumer.job_id,
                                }
                            )
                            if produced.handle_id in seen_handles:
                                continue
                            seen_handles.add(produced.handle_id)
                            await _add_node(produced)
                            next_frontier_ids.append(produced.handle_id)
                if not next_frontier_ids:
                    break
                frontier_ids = next_frontier_ids

        return {
            "root": handle_id,
            "nodes": nodes,
            "edges": edges,
        }

    @app.get(
        "/api/jobs",
        response_model=list[JobRow],
        tags=["jobs"],
        summary="Recent jobs; filter by workflow / state / algorithm.",
    )
    async def list_jobs(
        workflow_id: str | None = None,
        state: str | None = None,
        algorithm_name: str | None = None,
        limit: int = 200,
        order: str = "desc",
    ) -> list[dict[str, Any]]:
        """Filters (all optional) AND-compose. ``order=asc|desc`` (default
        desc = newest first, matching the panel + Gallery)."""

        store: JobsStore = app.state.jobs_store
        return await store.list_recent(
            limit=limit,
            workflow_id=workflow_id,
            state=state,
            algorithm_name=algorithm_name,
            order=order,
        )

    @app.get(
        "/api/jobs/{job_id}",
        response_model=JobDetail,
        tags=["jobs"],
        summary="One job with full params + input_handles + fail info.",
    )
    async def get_job(job_id: str) -> dict[str, Any]:
        store: JobsStore = app.state.jobs_store
        job = await store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return _job_to_json(job)

    @app.get(
        "/api/jobs/{job_id}/log",
        response_model=LogTail,
        tags=["jobs"],
        summary="Tail persisted stdout / stderr for one job.",
    )
    async def tail_job_log(
        job_id: str,
        tail: int = 200,
        stream: str = "both",
    ) -> dict[str, Any]:
        """Server-side stored logs (append-only ``job_logs`` table).

        Frontends generally consume live lines over the WS ``log_chunk``
        broadcast. This REST endpoint is for agents / scripts that want
        the last ``tail`` lines of a completed run without holding a
        socket open. ``stream=stdout|stderr|both`` (default both,
        interleaved in insertion order — matches the wall-clock ordering
        the algorithm actually printed).
        """

        store: JobsStore = app.state.jobs_store
        # 404 rather than empty tail on unknown job so callers can tell
        # "no logs yet" from "no such job".
        job = await store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")

        logs: LogStore = app.state.logs
        try:
            data = await logs.tail(job_id, n=tail, stream=stream)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"job_id": job_id, **data}

    @app.post("/api/jobs/run")
    async def run_job(body: dict[str, Any]) -> dict[str, Any]:
        """Ad-hoc single-job trigger (MVP; the full graph runner is later).

        Body:
            algorithm_name, algorithm_version, params (dict),
            input_handles (dict), workflow_id (optional).
        """

        registry: NodeRegistry = app.state.registry
        store: JobsStore = app.state.jobs_store
        hub: FrontendHub = app.state.hub

        try:
            algorithm_name = body["algorithm_name"]
            algorithm_version = body["algorithm_version"]
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=f"missing field: {exc}") from exc

        params = body.get("params", {}) or {}
        input_handles = body.get("input_handles", {}) or {}
        workflow_id = body.get("workflow_id", f"adhoc-{uuid.uuid4()}")

        session = registry.find_session_for_pack(algorithm_name, algorithm_version)
        if session is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"no online node has pack {algorithm_name}@{algorithm_version}; "
                    "check /api/nodes"
                ),
            )

        job = Job(
            job_id=str(uuid.uuid4()),
            workflow_id=workflow_id,
            snapshot_id=None,
            algorithm_name=algorithm_name,
            algorithm_version=algorithm_version,
            params=params,
            input_handles=input_handles,
            state=JobState.PENDING,
        )
        await store.create(job)
        _push_job_update(hub, job)

        # Transition to ASSIGNED and send to node.
        assigned = JobStateMachine.transition(job, JobState.ASSIGNED, node_id=session.node_id)
        kind, payload = event_from_transition(job, assigned)
        await store.update(assigned, kind, payload)
        _push_job_update(hub, assigned)

        assign_msg = JobAssign(
            job_id=assigned.job_id,
            workflow_id=assigned.workflow_id,
            algorithm_name=assigned.algorithm_name,
            algorithm_version=assigned.algorithm_version,
            params=assigned.params,
            input_handles=assigned.input_handles,
        )
        frame = encode("job_assign", assign_msg, v=session.protocol_v)
        async with session.send_lock:
            await session.ws.send_text(frame)

        return {
            "job_id": assigned.job_id,
            "node_id": session.node_id,
            "state": assigned.state.value,
        }

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str) -> dict[str, Any]:
        from hololab.protocol import JobCancel  # local import to avoid unused

        registry: NodeRegistry = app.state.registry
        store: JobsStore = app.state.jobs_store
        hub: FrontendHub = app.state.hub

        job = await store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        if job.node_id is None:
            raise HTTPException(status_code=409, detail="job has no assigned node")

        session = registry.get_session(job.node_id)
        if session is not None:
            frame = encode("job_cancel", JobCancel(job_id=job_id), v=session.protocol_v)
            async with session.send_lock:
                await session.ws.send_text(frame)

        # Optimistic transition — the node will confirm via job_fail(cancelled).
        try:
            new_job = JobStateMachine.transition(
                job, JobState.CANCELLED, fail_reason=JobFailReason.CANCELLED
            )
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        kind, payload = event_from_transition(job, new_job)
        await store.update(new_job, kind, payload)
        _push_job_update(hub, new_job)
        return {"job_id": job_id, "state": new_job.state.value}

    @app.get("/proxy/{node_id}/{path:path}")
    async def proxy(node_id: str, path: str, request: Request) -> Any:
        return await proxy_get(node_id, path, request, app.state.registry)

    @app.websocket("/ws/node")
    async def ws_node(ws: WebSocket) -> None:
        await ws.accept()
        await _handle_node_socket(app, ws)

    @app.websocket("/ws/frontend")
    async def ws_frontend(ws: WebSocket) -> None:
        await ws.accept()
        await _handle_frontend_socket(app, ws)


# ---------------------------------------------------------------------------
# Node socket handler
# ---------------------------------------------------------------------------


async def _handle_node_socket(app: FastAPI, ws: WebSocket) -> None:
    """Full lifecycle: register → route messages → cleanup on disconnect."""

    registry: NodeRegistry = app.state.registry
    store: JobsStore = app.state.jobs_store
    handles: HandleBook = app.state.handles
    hub: FrontendHub = app.state.hub
    logs: LogStore = app.state.logs

    session = None

    try:
        # Step 1: first frame must be register.
        raw = await ws.receive_text()
        env, payload = decode(raw)
        if env.kind != "register" or not isinstance(payload, Register):
            await ws.send_text(
                encode(
                    "register_err",
                    RegisterErr(code="protocol", message="first frame must be register"),
                )
            )
            await ws.close()
            return

        try:
            chosen_v = negotiate_version(payload.v_min, payload.v_max)
        except ValueError as exc:
            await ws.send_text(
                encode(
                    "register_err",
                    RegisterErr(code="version_mismatch", message=str(exc)),
                )
            )
            await ws.close()
            return

        try:
            session = await registry.register(
                ws=ws,
                node_name=payload.node_name,
                packs=payload.packs,
                gpu=payload.gpu,
                advertised_url=payload.advertised_url,
                protocol_v=chosen_v,
                node_id=payload.node_id,
                node_token=payload.node_token,
                workspace_root=payload.workspace_root,
                legacy_workspace_roots=payload.legacy_workspace_roots,
                flops_executor_id=payload.flops_executor_id,
            )
        except NodeAuthError as exc:
            log.warning(
                "node register auth failed",
                node_id=payload.node_id,
                node_name=payload.node_name,
                reason=str(exc),
            )
            await ws.send_text(
                encode(
                    "register_err",
                    RegisterErr(code="auth_failed", message=str(exc)),
                )
            )
            await ws.close()
            return

        ok = RegisterOk(
            node_id=session.node_id,
            session_id=session.session_id,
            protocol_v=chosen_v,
            node_token=session.token_issued,
        )
        async with session.send_lock:
            await ws.send_text(encode("register_ok", ok, v=chosen_v))

        log.info(
            "node connected",
            node_id=session.node_id,
            node_name=session.node_name,
            packs=len(session.packs),
        )

        hub.broadcast(
            encode(
                "node_online",
                NodeOnline(
                    node_id=session.node_id,
                    node_name=session.node_name,
                    packs=session.packs,
                ),
                v=chosen_v,
            )
        )

        # Step 2: process subsequent frames until disconnect.
        while True:
            raw = await ws.receive_text()
            try:
                env, payload = decode(raw)
            except ValueError as exc:
                log.warning("bad frame from node", node_id=session.node_id, error=str(exc))
                continue

            await _dispatch_node_frame(
                env.kind, payload, session, store, handles, hub, registry, logs, app
            )

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.warning("node socket error", error=str(exc), exc_info=True)
    finally:
        if session is not None:
            await registry.mark_offline(session.node_id)
            hub.broadcast(
                encode(
                    "node_offline",
                    NodeOffline(node_id=session.node_id, reason="disconnect"),
                    v=session.protocol_v,
                )
            )
            log.info("node disconnected", node_id=session.node_id)


async def _await_node_reply(
    app: FastAPI,
    session: Any,
    frame_kind: str,
    frame_payload: Any,
    req_id: str,
    *,
    pending_key: str = "pending_config_replies",
    timeout_s: float = 5.0,
) -> dict[str, Any]:
    """Send a request-frame to ``session``'s node and await its matching ``_resp``.

    Correlation is by ``req_id`` — the node echoes it verbatim in the
    response frame. The dispatcher (:func:`_dispatch_node_frame`)
    resolves the pending future when the reply arrives; we time out
    with 504 if the node doesn't answer within ``timeout_s``.

    ``pending_key`` names the ``app.state`` attribute holding the
    correlation dict for this frame family; different frame types use
    separate maps so a config-reply future isn't fed a stray
    artifact-check reply.
    """

    pending: dict[str, asyncio.Future[Any]] = getattr(app.state, pending_key)
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[dict[str, Any]] = loop.create_future()
    pending[req_id] = fut

    frame = encode(frame_kind, frame_payload, v=session.protocol_v)
    try:
        async with session.send_lock:
            await session.ws.send_text(frame)
    except Exception as exc:
        pending.pop(req_id, None)
        raise HTTPException(status_code=502, detail=f"send failed: {exc}") from exc

    try:
        return await asyncio.wait_for(fut, timeout=timeout_s)
    except asyncio.TimeoutError as exc:
        pending.pop(req_id, None)
        raise HTTPException(status_code=504, detail="node did not reply in time") from exc


# Backwards-compat shim — ``_await_config_reply`` still exists because
# tests import the config-reply flow by that name. Delegates to the
# generic helper.
async def _await_config_reply(
    app: FastAPI,
    session: Any,
    frame_kind: str,
    frame_payload: Any,
    req_id: str,
    *,
    timeout_s: float = 5.0,
) -> dict[str, Any]:
    return await _await_node_reply(
        app,
        session,
        frame_kind,
        frame_payload,
        req_id,
        pending_key="pending_config_replies",
        timeout_s=timeout_s,
    )


def _artifact_row_to_json(row: Any) -> dict[str, Any]:
    """Flat row → dict — kept next to the Artifacts endpoints for locality.

    ArtifactRow is a dataclass so ``dataclasses.asdict`` works, but going
    manual gives us a stable field order in the JSON response, which
    helps when diffing agent transcripts.
    """

    return {
        "handle_id": row.handle_id,
        "node_id": row.node_id,
        "node_name": row.node_name,
        "workflow_id": row.workflow_id,
        "workflow_name": row.workflow_name,
        "snapshot_id": row.snapshot_id,
        "job_id": row.job_id,
        "algorithm_name": row.algorithm_name,
        "algorithm_version": row.algorithm_version,
        "output_port_name": row.output_port_name,
        "tags": row.tags,
        "storage": row.storage,
        "path": row.path,
        "size_bytes": row.size_bytes,
        "created_ts": row.created_ts,
        "deleted_ts": row.deleted_ts,
        "state": row.state,
        "live_size_bytes": row.live_size_bytes,
        "live_mtime": row.live_mtime,
    }


async def _dispatch_node_frame(
    kind: str,
    payload: Any,
    session: Any,
    store: JobsStore,
    handles: HandleBook,
    hub: FrontendHub,
    registry: NodeRegistry,
    logs: LogStore,
    app: FastAPI,
) -> None:
    """Route an incoming frame from a connected node."""

    if kind == "heartbeat":
        assert isinstance(payload, Heartbeat)
        session.last_heartbeat_ts = time.time()
        return

    if kind in ("node_config_get_resp", "node_config_set_resp"):
        # Resolve the pending REST call keyed by the correlation ``req_id``.
        assert isinstance(payload, (NodeConfigGetResp, NodeConfigSetResp))
        pending = getattr(app.state, "pending_config_replies", None)
        if pending is not None:
            fut = pending.pop(payload.req_id, None)
            if fut is not None and not fut.done():
                fut.set_result(payload.model_dump())
        return

    if kind == "handle_check_resp":
        assert isinstance(payload, HandleCheckResp)
        pending = getattr(app.state, "pending_handle_checks", None)
        if pending is not None:
            fut = pending.pop(payload.req_id, None)
            if fut is not None and not fut.done():
                fut.set_result(payload.model_dump())
        return

    if kind == "artifact_delete_resp":
        assert isinstance(payload, ArtifactDeleteResp)
        pending = getattr(app.state, "pending_artifact_deletes", None)
        if pending is not None:
            fut = pending.pop(payload.req_id, None)
            if fut is not None and not fut.done():
                fut.set_result(payload.model_dump())
        return

    if kind == "packs_updated":
        assert isinstance(payload, PacksUpdated)
        await registry.replace_packs(session.node_id, payload.packs)
        # Notify frontend subscribers by re-emitting node_online with new packs.
        hub.broadcast(
            encode(
                "node_online",
                NodeOnline(
                    node_id=session.node_id,
                    node_name=session.node_name,
                    packs=payload.packs,
                ),
                v=session.protocol_v,
            )
        )
        log.info("packs updated", node_id=session.node_id, count=len(payload.packs))
        return

    if kind == "job_ack":
        assert isinstance(payload, JobAck)
        # State was set to ASSIGNED at dispatch; we don't require a formal ack
        # transition. The next real signal (log or progress) transitions to
        # RUNNING.
        return

    if kind == "job_progress":
        assert isinstance(payload, JobProgress)
        job = await store.get(payload.job_id)
        if job is None:
            return
        try:
            if job.state is JobState.ASSIGNED:
                new = JobStateMachine.transition(
                    job,
                    JobState.RUNNING,
                    progress_current=payload.current,
                    progress_total=payload.total,
                )
            else:
                # Same state; overwrite progress in place via a no-op transition.
                # We can't call transition() to stay in the same state (self-edges
                # are not modeled); write directly. Every field of ``job`` must
                # be preserved — graph_node_id in particular so downstream
                # ``job_update`` broadcasts still map onto the canvas node.
                new = Job(
                    job_id=job.job_id,
                    workflow_id=job.workflow_id,
                    snapshot_id=job.snapshot_id,
                    algorithm_name=job.algorithm_name,
                    algorithm_version=job.algorithm_version,
                    params=job.params,
                    input_handles=job.input_handles,
                    state=job.state,
                    node_id=job.node_id,
                    graph_node_id=job.graph_node_id,
                    progress_current=payload.current,
                    progress_total=payload.total,
                    fail_reason=job.fail_reason,
                    fail_exit_code=job.fail_exit_code,
                    fail_message=job.fail_message,
                    created_ts=job.created_ts,
                    updated_ts=time.time(),
                )
        except Exception:
            return
        await store.update(new, "progress", f'{{"c":{payload.current},"t":{payload.total}}}')
        _push_job_update(hub, new)
        return

    if kind == "job_log":
        assert isinstance(payload, JobLog)
        # Broadcast to frontends live, and persist to job_logs so the
        # REST tail endpoint can return them after the fact. Persistence
        # errors are logged but don't drop the broadcast — the live view
        # is what the user sees first.
        hub.broadcast(
            encode(
                "log_chunk",
                LogChunk(job_id=payload.job_id, lines=payload.lines, stream=payload.stream),
                v=session.protocol_v,
            )
        )
        try:
            await logs.append(payload.job_id, payload.stream, payload.lines)
        except Exception as exc:
            log.warning("job_log persist failed", job_id=payload.job_id, error=str(exc))
        # Nudge state to RUNNING if still ASSIGNED (first log line is a real signal).
        job = await store.get(payload.job_id)
        if job is not None and job.state is JobState.ASSIGNED:
            new = JobStateMachine.transition(job, JobState.RUNNING)
            kind_ev, ev_payload = event_from_transition(job, new)
            await store.update(new, kind_ev, ev_payload)
            _push_job_update(hub, new)
        return

    if kind == "job_done":
        assert isinstance(payload, JobDone)
        job = await store.get(payload.job_id)
        if job is None:
            return
        new = JobStateMachine.transition(job, JobState.DONE)
        kind_ev, ev_payload = event_from_transition(job, new)
        await store.update(new, kind_ev, ev_payload)
        # V8 attribution: record this done job in the snapshot_jobs
        # bridge so Continue/Fork dispatch and list_by_snapshot see it.
        # ``run_snapshot`` writes the same row (belt-and-braces —
        # INSERT OR IGNORE — but also covers ad-hoc / single-node
        # dispatch that goes straight through here without touching
        # ``run_snapshot``).
        if new.snapshot_id and new.graph_node_id:
            snapshot_jobs_store: SnapshotJobsStore = app.state.snapshot_jobs
            await snapshot_jobs_store.attribute(
                new.snapshot_id, new.job_id, new.graph_node_id
            )
        # Fetch the handles this job registered and include them on the
        # done broadcast so the frontend's preview drawer can open without
        # a follow-up REST round trip.
        done_handles = await handles.list_by_job(new.job_id)
        output_handles = {
            h.output_port_name: h.handle_id for h in done_handles if h.output_port_name
        }
        _push_job_update(hub, new, output_handles=output_handles or None)
        return

    if kind == "job_fail":
        assert isinstance(payload, JobFail)
        job = await store.get(payload.job_id)
        if job is None:
            return
        target = (
            JobState.CANCELLED if payload.reason is JobFailReason.CANCELLED else JobState.FAILED
        )
        try:
            new = JobStateMachine.transition(
                job,
                target,
                fail_reason=payload.reason,
                fail_exit_code=payload.exit_code,
                fail_message=payload.message,
            )
        except Exception as exc:
            log.warning("failed transition on job_fail", error=str(exc))
            return
        kind_ev, ev_payload = event_from_transition(job, new)
        await store.update(new, kind_ev, ev_payload)
        _push_job_update(hub, new)
        return

    if kind == "handle_register":
        assert isinstance(payload, HandleRegister)
        await handles.register(
            Handle(
                handle_id=payload.handle_id,
                node_id=payload.node_id,
                storage=payload.storage,
                tags=payload.tags,
                path=payload.path,
                size_bytes=payload.size_bytes,
                job_id=payload.job_id,
                output_port_name=payload.output_port_name,
            )
        )
        return

    if kind == "handle_locate_req":
        assert isinstance(payload, HandleLocateReq)
        handle = await handles.get(payload.handle_id)
        if handle is None:
            resp = HandleLocateResp(handle_id=payload.handle_id, not_found=True)
        elif handle.node_id == session.node_id:
            # Requester is the producer — use the local path directly, no HTTP.
            resp = HandleLocateResp(
                handle_id=payload.handle_id,
                node_id=handle.node_id,
                local_path=handle.path,
                storage=handle.storage,
            )
        else:
            producer = registry.get_session(handle.node_id)
            http_url: str | None = None
            if producer is not None and producer.advertised_url:
                http_url = producer.advertised_url.rstrip("/") + "/" + handle.path.lstrip("/")
            resp = HandleLocateResp(
                handle_id=payload.handle_id,
                node_id=handle.node_id,
                http_url=http_url,
                storage=handle.storage,
            )
        async with session.send_lock:
            await session.ws.send_text(encode("handle_locate_resp", resp, v=session.protocol_v))
        return

    log.debug("unhandled node frame", kind=kind)


# ---------------------------------------------------------------------------
# Frontend socket handler
# ---------------------------------------------------------------------------


async def _handle_frontend_socket(app: FastAPI, ws: WebSocket) -> None:
    hub: FrontendHub = app.state.hub
    registry: NodeRegistry = app.state.registry

    sub = hub.add(ws)

    # Send an initial snapshot so a fresh browser sees current nodes.
    for session in registry.all_sessions():
        try:
            sub.queue.put_nowait(
                encode(
                    "node_online",
                    NodeOnline(
                        node_id=session.node_id,
                        node_name=session.node_name,
                        packs=[
                            PackInventoryEntry(
                                name=p.name, version=p.version, manifest_hash=p.manifest_hash
                            )
                            for p in session.packs
                        ],
                    ),
                    v=session.protocol_v,
                )
            )
        except asyncio.QueueFull:  # pragma: no cover
            break

    pump_task = asyncio.create_task(hub.pump(sub))
    try:
        while True:
            # We don't currently accept frames from the frontend; drain and
            # ignore so the socket stays alive.
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.warning("frontend socket error", error=str(exc))
    finally:
        pump_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pump_task
        hub.remove(sub)


# ---------------------------------------------------------------------------
# Heartbeat sweeper
# ---------------------------------------------------------------------------


async def _heartbeat_sweeper(app: FastAPI) -> None:
    """Every few seconds, kick any node that hasn't sent a heartbeat in time."""

    registry: NodeRegistry = app.state.registry
    hub: FrontendHub = app.state.hub

    while True:
        await asyncio.sleep(HEARTBEAT_SWEEP_INTERVAL)
        now = time.time()
        for session in list(registry.all_sessions()):
            if now - session.last_heartbeat_ts > HEARTBEAT_DEAD_SECONDS:
                log.info("node heartbeat timeout", node_id=session.node_id)
                await registry.mark_offline(session.node_id)
                hub.broadcast(
                    encode(
                        "node_offline",
                        NodeOffline(node_id=session.node_id, reason="heartbeat_timeout"),
                        v=session.protocol_v,
                    )
                )
                with contextlib.suppress(Exception):
                    await session.ws.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _job_to_json(job: Job) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "workflow_id": job.workflow_id,
        "snapshot_id": job.snapshot_id,
        "node_id": job.node_id,
        "graph_node_id": job.graph_node_id,
        "algorithm_name": job.algorithm_name,
        "algorithm_version": job.algorithm_version,
        "params": job.params,
        "input_handles": job.input_handles,
        "state": job.state.value,
        "progress": (
            {"current": job.progress_current, "total": job.progress_total}
            if job.progress_current is not None
            else None
        ),
        "fail_reason": job.fail_reason.value if job.fail_reason else None,
        "fail_exit_code": job.fail_exit_code,
        "fail_message": job.fail_message,
        "created_ts": job.created_ts,
        "updated_ts": job.updated_ts,
    }


async def _list_consumer_jobs(store: JobsStore, handle_id: str) -> list[Job]:
    """Return every job whose ``input_handles`` map contains ``handle_id``.

    We use a plain SQL LIKE over ``input_handles_json`` — there's no
    dedicated input-edges index. For the sizes we care about (thousands
    of jobs at most) that scan is trivial; a fleet with millions of
    jobs would want a normalised ``job_inputs`` bridge table instead.
    Empty result is fine (root artifact has no descendants yet).
    """

    async with (
        store._db.read() as conn,
        conn.execute(
            """
            SELECT job_id
            FROM jobs
            WHERE input_handles_json LIKE ?
            """,
            (f"%{handle_id}%",),
        ) as cur,
    ):
        rows = await cur.fetchall()
    result: list[Job] = []
    for r in rows:
        job = await store.get(r[0])
        if job is None:
            continue
        if handle_id in (job.input_handles or {}).values():
            result.append(job)
    return result


def _push_job_update(
    hub: FrontendHub,
    job: Job,
    *,
    output_handles: dict[str, str] | None = None,
) -> None:
    """Broadcast a compact ``job_update`` frame to all frontends.

    Callers that transition a job to ``done`` may pre-fetch its output
    handles and pass them in — the frontend uses this to open the node's
    preview drawer without a follow-up REST call.
    """

    update = JobUpdate(
        job_id=job.job_id,
        state=job.state.value,
        workflow_id=job.workflow_id,
        algorithm_name=job.algorithm_name,
        algorithm_version=job.algorithm_version,
        graph_node_id=job.graph_node_id,
        output_handles=output_handles,
        progress=(
            JobProgress(
                job_id=job.job_id,
                current=job.progress_current or 0,
                total=job.progress_total or 0,
            )
            if job.progress_current is not None
            else None
        ),
        fail=(
            JobFail(
                job_id=job.job_id,
                reason=job.fail_reason,
                exit_code=job.fail_exit_code,
                message=job.fail_message,
            )
            if job.fail_reason
            else None
        ),
    )
    hub.broadcast(encode("job_update", update))


# ----------------------------------------------------------------------------
# The ``JSONResponse`` re-export makes it easier to mock in tests.
# ----------------------------------------------------------------------------
__all__ = ["JSONResponse", "create_app"]
