"""Node registry — connected sessions + persistent node/pack rows."""

from __future__ import annotations

import asyncio
import json
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import aiosqlite
from fastapi import WebSocket

from hololab.persistence.db import Database
from hololab.protocol.messages import GpuInfo, PackInventoryEntry


class NodeAuthError(Exception):
    """Raised when a Register frame presents a known node_id with the wrong token.

    The socket handler converts this into a ``RegisterErr(code=auth_failed)``
    frame and closes the connection.
    """


@dataclass
class NodeSession:
    """One active WebSocket session with a node.

    A ``NodeSession`` is process-local; it is not persisted. The persistent
    ``nodes`` row survives across sessions.

    ``workspace_root`` is the absolute path on the node under which its
    file server serves. The gateway uses it to compute preview proxy URLs
    (see :func:`hololab.gateway.app.handle_lookup`).
    """

    node_id: str
    session_id: str
    node_name: str
    ws: WebSocket
    advertised_url: str | None
    gpu: GpuInfo
    packs: list[PackInventoryEntry]
    protocol_v: int
    workspace_root: str | None = None
    # Extra roots the node's file server ALSO serves (read-only for old
    # artifacts). The gateway tries stripping each of these when
    # computing preview proxy URLs so a handle produced under the old
    # root still resolves after the node has been relocated.
    legacy_workspace_roots: list[str] = field(default_factory=list)
    # Cobrowser integration — mirrored from the node's config.yaml via
    # the register frame (or refreshed by ``PATCH /api/nodes/{id}/config``
    # via node_config_set_req). The frontend reads it off
    # ``GET /api/nodes`` and hands it to ``window.flops.showDocument``
    # as ``deviceId`` for the "Open in Cocoder" button. Null when the
    # operator hasn't set it. See docs/cobrowser-integration.md.
    flops_executor_id: str | None = None
    # Primary packs directory. In the multi-pack-source world this is
    # ``pack_dirs[0]`` — kept as its own field for the "Jump to
    # source" manifest.yaml fallback (which resolves against a single
    # canonical root: the first one) and for backward-compat with
    # older frontends that read the scalar.
    packs_dir: str | None = None
    # Full list of pack source directories the node scans. Populated
    # by the modern Register frame; when a legacy node sends only
    # scalar ``packs_dir``, the registry wraps it in a single-entry
    # list at register time.
    pack_dirs: list[str] = field(default_factory=list)
    # ``token_issued`` is set when this session's register frame either
    # minted a fresh node_token or the gateway is (re-)issuing one. The
    # socket handler forwards it in RegisterOk so the node can persist it
    # to config.yaml. For steady-state reconnects it stays None.
    token_issued: str | None = None
    connected_ts: float = field(default_factory=time.time)
    last_heartbeat_ts: float = field(default_factory=time.time)
    # Simple lock for send() so background tasks don't race on the socket.
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class NodeRegistry:
    """Tracks connected node sessions and persists node/pack metadata."""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._sessions: dict[str, NodeSession] = {}  # node_id → session

    # -- session lifecycle ---------------------------------------------------

    async def register(
        self,
        *,
        ws: WebSocket,
        node_name: str,
        packs: list[PackInventoryEntry],
        gpu: GpuInfo,
        advertised_url: str | None,
        protocol_v: int,
        node_id: str | None,
        node_token: str | None = None,
        workspace_root: str | None = None,
        legacy_workspace_roots: list[str] | None = None,
        flops_executor_id: str | None = None,
        packs_dir: str | None = None,
        pack_dirs: list[str] | None = None,
    ) -> NodeSession:
        """Register a node, applying the three-path identity rule:

        1. **Claim (id + matching token)** — the node presents both an id
           and a token that matches the stored one. Refresh session state
           on the existing row; ``token_issued`` on the session is None
           because nothing new needs to be sent back.

        2. **Reject (id + wrong token)** — someone is presenting a known
           id with the wrong secret. We raise :class:`NodeAuthError`; the
           caller renders a ``RegisterErr(code=auth_failed)`` and closes.
           The one benign exception is a legacy row with a NULL stored
           token: we treat that as "token not established yet" and mint
           one on this register (see path 3b).

        3. **Mint (no id, or id-not-in-DB, or legacy NULL-token row)** —
           mint a fresh ``node_token`` and, if the caller didn't bring an
           id, a fresh ``node_id`` too. The new token is placed on the
           session so the socket handler forwards it in RegisterOk for the
           node to persist.

        A fresh ``session_id`` is always issued regardless of path.

        Raises:
            NodeAuthError: on path 2 (id known, token wrong).
        """

        session_id = str(uuid.uuid4())
        now = time.time()
        gpu_json = gpu.model_dump_json()

        stored_token: str | None = None
        row_exists = False
        if node_id:
            async with (
                self._db.read() as conn,
                conn.execute("SELECT node_token FROM nodes WHERE node_id=?", (node_id,)) as cur,
            ):
                row = await cur.fetchone()
            if row is not None:
                row_exists = True
                stored_token = row[0]

        token_issued: str | None = None
        if not node_id or not row_exists:
            # Path 3a: fresh install (no id) OR id from a DB the gateway
            # has since lost (rare — e.g. gateway wiped its sqlite while
            # the node kept its config.yaml). Mint everything.
            node_id = node_id or str(uuid.uuid4())
            token_issued = secrets.token_urlsafe(32)
            effective_token = token_issued
        elif stored_token is None:
            # Path 3b: row exists but has no token yet — legacy pre-v4
            # registration or a token-less first register that predates
            # this scheme. Mint a token now and store it.
            token_issued = secrets.token_urlsafe(32)
            effective_token = token_issued
        elif node_token == stored_token:
            # Path 1: rightful owner reconnecting. No new secret to issue.
            effective_token = stored_token
        else:
            # Path 2: id known, presented token wrong (or missing). Do NOT
            # rotate the stored token — the real owner needs to keep using
            # theirs. The socket handler will reject this connection.
            raise NodeAuthError(
                f"node_id {node_id!r} exists but the presented node_token does not match"
            )

        async def _write(conn: aiosqlite.Connection) -> None:
            await conn.execute(
                """
                INSERT INTO nodes (node_id, node_name, session_id, advertised_url, gpu_json,
                                   last_seen_ts, created_ts, online, node_token)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    node_name=excluded.node_name,
                    session_id=excluded.session_id,
                    advertised_url=excluded.advertised_url,
                    gpu_json=excluded.gpu_json,
                    last_seen_ts=excluded.last_seen_ts,
                    online=1,
                    node_token=COALESCE(nodes.node_token, excluded.node_token)
                """,
                (
                    node_id,
                    node_name,
                    session_id,
                    advertised_url,
                    gpu_json,
                    now,
                    now,
                    effective_token,
                ),
            )
            await conn.execute("DELETE FROM packs WHERE node_id=?", (node_id,))
            for pk in packs:
                await conn.execute(
                    "INSERT INTO packs (node_id, name, version, manifest_hash) VALUES (?, ?, ?, ?)",
                    (node_id, pk.name, pk.version, pk.manifest_hash),
                )

        await self._db.write(_write)

        session = NodeSession(
            node_id=node_id,
            session_id=session_id,
            node_name=node_name,
            ws=ws,
            advertised_url=advertised_url,
            gpu=gpu,
            packs=list(packs),
            protocol_v=protocol_v,
            workspace_root=workspace_root,
            legacy_workspace_roots=list(legacy_workspace_roots or []),
            flops_executor_id=flops_executor_id,
            packs_dir=packs_dir,
            # Multi-pack-source: prefer the modern list; fall back to
            # wrapping a legacy scalar so the wire remains consistent.
            pack_dirs=list(pack_dirs) if pack_dirs else ([packs_dir] if packs_dir else []),
            token_issued=token_issued,
        )
        # If there was an old session for this node_id, drop it silently — the
        # network layer will notice the old socket is dead soon.
        self._sessions[node_id] = session
        return session

    async def replace_packs(self, node_id: str, packs: list[PackInventoryEntry]) -> None:
        """Replace the pack inventory for an already-connected node.

        Called when the node's on-disk packs change (add/remove/edit manifest)
        and it emits a ``packs_updated`` frame.
        """

        session = self._sessions.get(node_id)
        if session is not None:
            session.packs = list(packs)

        async def _write(conn: aiosqlite.Connection) -> None:
            await conn.execute("DELETE FROM packs WHERE node_id=?", (node_id,))
            for pk in packs:
                await conn.execute(
                    "INSERT INTO packs (node_id, name, version, manifest_hash) VALUES (?, ?, ?, ?)",
                    (node_id, pk.name, pk.version, pk.manifest_hash),
                )

        await self._db.write(_write)

    async def mark_offline(self, node_id: str) -> None:
        """Persist node offline flag. Idempotent."""

        async def _write(conn: aiosqlite.Connection) -> None:
            await conn.execute(
                "UPDATE nodes SET online=0 WHERE node_id=?",
                (node_id,),
            )

        await self._db.write(_write)
        self._sessions.pop(node_id, None)

    async def touch_heartbeat(self, node_id: str) -> None:
        """Update the in-memory last-heartbeat and persist ``last_seen_ts``."""

        session = self._sessions.get(node_id)
        if session is not None:
            session.last_heartbeat_ts = time.time()
        now = time.time()

        async def _write(conn: aiosqlite.Connection) -> None:
            await conn.execute(
                "UPDATE nodes SET last_seen_ts=? WHERE node_id=?",
                (now, node_id),
            )

        await self._db.write(_write)

    # -- lookups -------------------------------------------------------------

    def get_session(self, node_id: str) -> NodeSession | None:
        return self._sessions.get(node_id)

    def all_sessions(self) -> list[NodeSession]:
        return list(self._sessions.values())

    def find_session_for_pack(
        self, algorithm_name: str, algorithm_version: str
    ) -> NodeSession | None:
        """MVP scheduler: pick the first online node that has this pack.

        Real scheduling (affinity, load, GPU) lands later.
        """

        for session in self._sessions.values():
            for pk in session.packs:
                if pk.name == algorithm_name and pk.version == algorithm_version:
                    return session
        return None

    def get_output_dim_labels(
        self, algorithm_name: str, algorithm_version: str, output_port_name: str
    ) -> list[str] | None:
        """Return the declared ``dim_labels`` for one output port.

        Returns ``None`` when the pack isn't available on any live session
        (offline node, pack removed), or when the port isn't declared, or
        when the manifest file can't be loaded. Callers should treat that
        as "unknown depth" — not "scalar".

        Used by the handle-summary endpoint to compute how many levels
        deep to walk when reporting ``dim_sizes``.
        """

        from pathlib import Path as _Path

        from hololab.manifest import load_manifest

        legacy_root = _Path.cwd() / "packs"
        for session in self._sessions.values():
            for pk in session.packs:
                if pk.name != algorithm_name or pk.version != algorithm_version:
                    continue
                candidates: list[_Path] = []
                if pk.manifest_path:
                    candidates.append(_Path(pk.manifest_path))
                if pk.source_dir:
                    sd = _Path(pk.source_dir)
                    if sd.is_file():
                        candidates.append(sd)
                    else:
                        candidates.append(sd / "manifest.yaml")
                for root in [*list(session.pack_dirs), str(legacy_root)]:
                    candidates.append(_Path(root) / f"{pk.name}@{pk.version}" / "manifest.yaml")
                for p in candidates:
                    if not p.is_file():
                        continue
                    try:
                        manifest, _sha = load_manifest(p)
                    except Exception:
                        continue
                    port = manifest.outputs.get(output_port_name)
                    if port is None:
                        return None
                    return list(port.dim_labels)
        return None

    async def as_summary_json(self) -> list[dict[str, Any]]:
        """Cheap dump of currently-connected nodes for the frontend."""

        out: list[dict[str, Any]] = []
        for s in self._sessions.values():
            out.append(
                {
                    "node_id": s.node_id,
                    "node_name": s.node_name,
                    "gpu": s.gpu.model_dump(),
                    "packs": [p.model_dump() for p in s.packs],
                    "connected_ts": s.connected_ts,
                    "advertised_url": s.advertised_url,
                    "workspace_root": s.workspace_root,
                    "legacy_workspace_roots": list(s.legacy_workspace_roots),
                    "flops_executor_id": s.flops_executor_id,
                    "packs_dir": s.packs_dir,
                    "pack_dirs": list(s.pack_dirs),
                }
            )
        return out

    async def load_persisted_packs_json(self) -> list[dict[str, Any]]:
        """List all packs recorded in DB, even for offline nodes."""

        async with (
            self._db.read() as conn,
            conn.execute(
                """
                SELECT p.node_id, p.name, p.version, p.manifest_hash,
                       n.node_name, n.online
                FROM packs p
                LEFT JOIN nodes n USING(node_id)
                ORDER BY p.name, p.version
                """
            ) as cur,
        ):
            rows = await cur.fetchall()
        return [
            {
                "node_id": r[0],
                "name": r[1],
                "version": r[2],
                "manifest_hash": r[3],
                "node_name": r[4],
                "online": bool(r[5]),
            }
            for r in rows
        ]

    def catalog_json(self) -> list[dict[str, Any]]:
        """Aggregated pack catalog with full port/param signatures.

        Deduped by (name, version) across all connected nodes so the frontend
        gets one entry per pack, plus the list of ``node_ids`` that currently
        offer it (for the assignment dropdown).

        This only sees packs from currently-connected sessions, which is
        exactly what the palette should show — you can't place a pack that
        no online node can execute.
        """

        # Local imports to avoid a top-level cycle: pack loading pulls in the
        # manifest package which itself imports pydantic — cheap but per-call
        # is fine because this is called from a REST handler, not a hot path.
        from hololab.manifest import load_manifest

        # (name, version) -> catalog entry
        by_key: dict[tuple[str, str], dict[str, Any]] = {}

        # We need the actual manifest for tags/params. Sessions carry only a
        # hash + name/version, so we walk each connected node's local packs
        # dir? No — the manifest lives on the node, not on the gateway. For
        # MVP, the palette relies on packs installed under the gateway host
        # too (dev/all-in-one). Multi-machine deployments will need a
        # ``manifest_yaml`` field pushed in ``packs_updated`` (deferred).
        # This is documented in docs/workflow-schema.md non-goals.

        # Practical workaround: walk pack roots on the gateway host to load
        # manifests by name+version. In the all-in-one deploy the gateway
        # and node share disk, so this works today.
        #
        # Preferred: use ``manifest_path`` from the pack inventory — the
        # node reported the exact resolved path (supports the polymorphic
        # pack_dirs contract). Fall back to guessing under source_dir /
        # session pack_dirs / legacy ``./packs`` for pre-manifest_path
        # nodes.
        from pathlib import Path as _Path

        legacy_root = _Path.cwd() / "packs"

        def _try_load(
            name: str,
            version: str,
            manifest_path: str | None,
            source_dir: str | None,
            roots: list[str],
        ) -> Any:
            if manifest_path:
                p = _Path(manifest_path)
                if p.is_file():
                    m, _sha = load_manifest(p)
                    return m
            candidates: list[_Path] = []
            if source_dir:
                sd = _Path(source_dir)
                # ``source_dir`` may itself point directly at a manifest
                # file in precise-file mode — check that first.
                if sd.is_file():
                    m, _sha = load_manifest(sd)
                    return m
                candidates.append(sd)
            for r in roots:
                p = _Path(r)
                if p not in candidates:
                    candidates.append(p)
            if legacy_root not in candidates:
                candidates.append(legacy_root)
            for root in candidates:
                path = root / f"{name}@{version}" / "manifest.yaml"
                if path.is_file():
                    m, _sha = load_manifest(path)
                    return m
            return None

        for session in self._sessions.values():
            for pk in session.packs:
                key = (pk.name, pk.version)
                entry = by_key.setdefault(
                    key,
                    {
                        "name": pk.name,
                        "version": pk.version,
                        "manifest_hash": pk.manifest_hash,
                        "node_ids": [],
                        "inputs": {},
                        "outputs": {},
                        "params": {},
                        "description": None,
                        "category": [],
                        "docs": None,
                        "source_entry": None,
                        "manifest_path": pk.manifest_path,
                        "source_dir": pk.source_dir,
                        "arrayable": False,
                    },
                )
                if session.node_id not in entry["node_ids"]:
                    entry["node_ids"].append(session.node_id)

                # Populate signature once, from the manifest on disk.
                if not entry["outputs"]:
                    manifest = _try_load(
                        pk.name,
                        pk.version,
                        pk.manifest_path,
                        pk.source_dir,
                        list(session.pack_dirs),
                    )
                    if manifest is not None:
                        entry["description"] = manifest.description
                        entry["category"] = list(manifest.category)
                        entry["docs"] = manifest.docs
                        entry["source_entry"] = manifest.source_entry
                        entry["arrayable"] = manifest.arrayable
                        entry["inputs"] = {
                            n: {
                                "tags": s.tags,
                                "required": s.required,
                                "storage": s.storage.value,
                                "description": s.description,
                                "arrayed": s.arrayed,
                                "scalar": s.scalar,
                                "dim_labels": list(s.dim_labels),
                            }
                            for n, s in manifest.inputs.items()
                        }
                        from hololab.gateway.tag_viewers import (
                            infer_preview_for_output,
                        )

                        entry["outputs"] = {
                            n: {
                                "tags": s.tags,
                                "storage": s.storage.value,
                                "description": s.description,
                                "arrayed": s.arrayed,
                                "scalar": s.scalar,
                                "tags_from": s.tags_from,
                                "dim_labels": list(s.dim_labels),
                                # Tag → viewer inference: if the manifest
                                # didn't declare an explicit ``preview:``
                                # block, look at the port's tags and
                                # pull the canonical viewer for that
                                # data class from the registry. Explicit
                                # declaration always wins.
                                "preview": (
                                    inferred.model_dump()
                                    if (
                                        inferred := infer_preview_for_output(
                                            s.preview, list(s.tags)
                                        )
                                    )
                                    is not None
                                    else None
                                ),
                            }
                            for n, s in manifest.outputs.items()
                        }
                        entry["params"] = {
                            n: {
                                "type": s.type.value,
                                "default": s.default,
                                "description": s.description,
                                "optional": s.optional,
                            }
                            for n, s in manifest.params.items()
                        }

        return sorted(by_key.values(), key=lambda e: (e["name"], e["version"]))


# Rehydrate an offline flag on startup (in case gateway restarted uncleanly).
async def reset_all_online_flags(db: Database) -> None:
    """Set every node's ``online`` flag to 0. Called once at gateway startup."""

    async def _write(conn: aiosqlite.Connection) -> None:
        await conn.execute("UPDATE nodes SET online=0")

    await db.write(_write)


async def mark_stuck_jobs_orphaned(db: Database) -> int:
    """Mark ``assigned`` / ``running`` jobs as ``orphaned`` on gateway startup.

    Rationale: the gateway loses in-memory session state on restart,
    but the node's subprocess is a separate process and typically
    survives. Historically we marked every in-flight row ``interrupted``
    (terminal, no coming back) and lost work by the shovelful during
    dev-time gateway bounces. Now we mark them ``orphaned`` — a
    recoverable state — and rely on two reconciliation paths to finalise:

    * ``_handle_node_socket`` matches the node's live ``running_jobs``
      against our ``orphaned`` rows at register time and hoists still-
      alive ones back to ``running`` (and non-claimed ones to
      ``interrupted``, since if the node's daemon doesn't remember
      running them their terminal frames were lost).
    * A background sweeper finalises any ``orphaned`` row whose
      owning node hasn't reconnected within ``ORPHAN_GRACE_S`` seconds.

    ``pending`` rows are deliberately left alone: they weren't in-flight,
    they were just queued. The scheduler picks them up again on next
    tick; forcing them to ``interrupted`` used to make queue-during-crash
    dispatches vanish, which was another slice of the same problem.

    Returns the number of rows flipped. Called from ``_startup`` before
    the sweeper starts; safe to run on an already-clean DB.
    """

    reclaimable = ("assigned", "running")

    async def _write(conn: aiosqlite.Connection) -> int:
        placeholders = ",".join("?" for _ in reclaimable)
        cur = await conn.execute(
            f"UPDATE jobs SET state='orphaned', updated_ts=? WHERE state IN ({placeholders})",
            (time.time(), *reclaimable),
        )
        return cur.rowcount or 0

    return await db.write(_write)


async def finalize_stale_orphaned_jobs(
    db: Database,
    *,
    cutoff_ts: float,
    exclude_node_ids: set[str] | None = None,
) -> list[str]:
    """Sweep ``orphaned`` rows whose owner never came back to ``interrupted``.

    A grace-window sweeper — call this periodically from the gateway
    main loop. ``cutoff_ts`` is the age boundary: any ``orphaned`` row
    whose ``updated_ts`` is older than this gets flipped to
    ``interrupted``. ``exclude_node_ids`` names nodes that ARE currently
    connected (recently reconciled at register time); the sweeper
    skips their orphans so a slow reconcile doesn't race the sweeper.

    Returns the job_ids that were finalised so the caller can broadcast
    ``job_update`` frames for each — the frontend needs to flip its dot
    from orphaned-blue to interrupted-grey.
    """

    async def _write(conn: aiosqlite.Connection) -> list[str]:
        # First: collect the ids we're about to flip (SQLite doesn't
        # give us RETURNING in the version we support).
        where = "state='orphaned' AND updated_ts < ?"
        params: list[Any] = [cutoff_ts]
        if exclude_node_ids:
            placeholders = ",".join("?" for _ in exclude_node_ids)
            where += f" AND (node_id IS NULL OR node_id NOT IN ({placeholders}))"
            params.extend(exclude_node_ids)
        async with conn.execute(
            f"SELECT job_id FROM jobs WHERE {where}",
            params,
        ) as cur:
            ids = [row[0] for row in await cur.fetchall()]
        if not ids:
            return []
        now = time.time()
        placeholders = ",".join("?" for _ in ids)
        await conn.execute(
            (f"UPDATE jobs SET state='interrupted', updated_ts=? WHERE job_id IN ({placeholders})"),
            (now, *ids),
        )
        return ids

    return await db.write(_write)


# -- jobs store: thin CRUD over the ``jobs`` and ``job_events`` tables --------


class JobsStore:
    """CRUD for :class:`hololab.gateway.jobs.Job` rows + event append."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(self, job: Job) -> None:  # noqa: F821 - forward ref
        from hololab.gateway.jobs import Job, JobState  # local import to avoid cycle

        assert isinstance(job, Job)

        # If the caller created a job that's already done and has both a
        # snapshot_id and a graph_node_id, we also write the V8
        # snapshot_jobs attribution row. Production creates go through
        # ``pending → done`` transitions and the WS handler writes the
        # attribution at DONE; direct-create-in-done-state (test seeds,
        # historical replay, rerun-from's synthetic bookkeeping) needs
        # us to do it here.
        also_attribute = (
            job.state is JobState.DONE
            and job.snapshot_id is not None
            and job.graph_node_id is not None
        )

        async def _write(conn: aiosqlite.Connection) -> None:
            await conn.execute(
                """
                INSERT INTO jobs
                    (job_id, snapshot_id, workflow_id, node_id, graph_node_id,
                     algorithm_name, algorithm_version, params_json, input_handles_json,
                     state, progress_current, progress_total, fail_reason, fail_exit_code,
                     fail_message, created_ts, updated_ts, reused_from_job_id,
                     parent_job_id, shard_element_id, started_ts, expected_shards)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.snapshot_id,
                    job.workflow_id,
                    job.node_id,
                    job.graph_node_id,
                    job.algorithm_name,
                    job.algorithm_version,
                    json.dumps(job.params),
                    json.dumps(job.input_handles),
                    job.state.value,
                    job.progress_current,
                    job.progress_total,
                    job.fail_reason.value if job.fail_reason else None,
                    job.fail_exit_code,
                    job.fail_message,
                    job.created_ts,
                    job.updated_ts,
                    job.reused_from_job_id,
                    job.parent_job_id,
                    job.shard_element_id,
                    job.started_ts,
                    job.expected_shards,
                ),
            )
            await conn.execute(
                "INSERT INTO job_events (job_id, kind, payload_json, ts) VALUES (?, ?, ?, ?)",
                (job.job_id, "created", json.dumps({"state": job.state.value}), job.created_ts),
            )
            if also_attribute:
                await conn.execute(
                    """
                    INSERT OR IGNORE INTO snapshot_jobs
                        (snapshot_id, job_id, graph_node_id)
                    VALUES (?, ?, ?)
                    """,
                    (job.snapshot_id, job.job_id, job.graph_node_id),
                )

        await self._db.write(_write)

    async def update(self, job: Job, event_kind: str, event_payload_json: str) -> None:  # noqa: F821
        # V8 attribution: when a job transitions INTO done and has both
        # snapshot_id + graph_node_id, we also insert the snapshot_jobs
        # row here. The WS ``job_done`` handler also writes it (belt-and-
        # braces via INSERT OR IGNORE); this covers callers that bypass
        # the WS path (tests, adhoc jobs going through JobStateMachine
        # directly).
        from hololab.gateway.jobs import JobState  # local import to avoid cycle

        also_attribute = (
            job.state is JobState.DONE
            and job.snapshot_id is not None
            and job.graph_node_id is not None
        )

        async def _write(conn: aiosqlite.Connection) -> None:
            await conn.execute(
                """
                UPDATE jobs SET
                    node_id=?,
                    state=?,
                    progress_current=?,
                    progress_total=?,
                    fail_reason=?,
                    fail_exit_code=?,
                    fail_message=?,
                    updated_ts=?,
                    started_ts=COALESCE(started_ts, ?)
                WHERE job_id=?
                """,
                (
                    job.node_id,
                    job.state.value,
                    job.progress_current,
                    job.progress_total,
                    job.fail_reason.value if job.fail_reason else None,
                    job.fail_exit_code,
                    job.fail_message,
                    job.updated_ts,
                    job.started_ts,
                    job.job_id,
                ),
            )
            await conn.execute(
                "INSERT INTO job_events (job_id, kind, payload_json, ts) VALUES (?, ?, ?, ?)",
                (job.job_id, event_kind, event_payload_json, job.updated_ts),
            )
            if also_attribute:
                await conn.execute(
                    """
                    INSERT OR IGNORE INTO snapshot_jobs
                        (snapshot_id, job_id, graph_node_id)
                    VALUES (?, ?, ?)
                    """,
                    (job.snapshot_id, job.job_id, job.graph_node_id),
                )

        await self._db.write(_write)

    async def get(self, job_id: str) -> Job | None:  # noqa: F821
        from hololab.gateway.jobs import Job, JobState
        from hololab.protocol.messages import JobFailReason

        async with (
            self._db.read() as conn,
            conn.execute(
                """
                SELECT job_id, snapshot_id, workflow_id, node_id, graph_node_id,
                       algorithm_name, algorithm_version, params_json, input_handles_json,
                       state, progress_current, progress_total, fail_reason, fail_exit_code,
                       fail_message, created_ts, updated_ts, reused_from_job_id,
                       parent_job_id, shard_element_id, started_ts, expected_shards
                FROM jobs WHERE job_id=?
                """,
                (job_id,),
            ) as cur,
        ):
            row = await cur.fetchone()

        if row is None:
            return None
        return Job(
            job_id=row[0],
            snapshot_id=row[1],
            workflow_id=row[2],
            node_id=row[3],
            graph_node_id=row[4],
            algorithm_name=row[5],
            algorithm_version=row[6],
            params=json.loads(row[7]),
            input_handles=json.loads(row[8]),
            state=JobState(row[9]),
            progress_current=row[10],
            progress_total=row[11],
            fail_reason=JobFailReason(row[12]) if row[12] else None,
            fail_exit_code=row[13],
            fail_message=row[14],
            created_ts=row[15],
            updated_ts=row[16],
            reused_from_job_id=row[17],
            parent_job_id=row[18],
            shard_element_id=row[19],
            started_ts=row[20],
            expected_shards=row[21],
        )

    async def list_shards_of(self, parent_job_id: str) -> list[Job]:  # noqa: F821
        """Return every shard job attributed to the given parent, oldest first.

        Used by the fan-in step of arrayed<T> fan-out to collect all shard
        outputs before registering the aggregate parent handle. Ordered by
        ``created_ts`` so the shard list mirrors the sequential dispatch
        order the scheduler produced.
        """

        from hololab.gateway.jobs import Job, JobState
        from hololab.protocol.messages import JobFailReason

        async with (
            self._db.read() as conn,
            conn.execute(
                """
                SELECT job_id, snapshot_id, workflow_id, node_id, graph_node_id,
                       algorithm_name, algorithm_version, params_json, input_handles_json,
                       state, progress_current, progress_total, fail_reason, fail_exit_code,
                       fail_message, created_ts, updated_ts, reused_from_job_id,
                       parent_job_id, shard_element_id, started_ts, expected_shards
                FROM jobs
                WHERE parent_job_id=?
                ORDER BY created_ts ASC
                """,
                (parent_job_id,),
            ) as cur,
        ):
            rows = await cur.fetchall()

        return [
            Job(
                job_id=r[0],
                snapshot_id=r[1],
                workflow_id=r[2],
                node_id=r[3],
                graph_node_id=r[4],
                algorithm_name=r[5],
                algorithm_version=r[6],
                params=json.loads(r[7]),
                input_handles=json.loads(r[8]),
                state=JobState(r[9]),
                progress_current=r[10],
                progress_total=r[11],
                fail_reason=JobFailReason(r[12]) if r[12] else None,
                fail_exit_code=r[13],
                fail_message=r[14],
                created_ts=r[15],
                updated_ts=r[16],
                reused_from_job_id=r[17],
                parent_job_id=r[18],
                shard_element_id=r[19],
                started_ts=r[20],
                expected_shards=r[21],
            )
            for r in rows
        ]

    async def list_orphaned_for_node(self, node_id: str) -> list[Job]:  # noqa: F821
        """Return every ``orphaned`` job owned by the given node.

        Used at register-time reconciliation to match the node's live
        ``running_jobs`` claim against DB rows that were flipped to
        ``orphaned`` at gateway startup. Result order matches
        ``created_ts`` ASC so the caller processes shards in dispatch
        order — useful for logs but not semantically required.
        """

        from hololab.gateway.jobs import Job, JobState
        from hololab.protocol.messages import JobFailReason

        async with (
            self._db.read() as conn,
            conn.execute(
                """
                SELECT job_id, snapshot_id, workflow_id, node_id, graph_node_id,
                       algorithm_name, algorithm_version, params_json, input_handles_json,
                       state, progress_current, progress_total, fail_reason, fail_exit_code,
                       fail_message, created_ts, updated_ts, reused_from_job_id,
                       parent_job_id, shard_element_id, started_ts, expected_shards
                FROM jobs
                WHERE state='orphaned' AND node_id=?
                ORDER BY created_ts ASC
                """,
                (node_id,),
            ) as cur,
        ):
            rows = await cur.fetchall()

        return [
            Job(
                job_id=r[0],
                snapshot_id=r[1],
                workflow_id=r[2],
                node_id=r[3],
                graph_node_id=r[4],
                algorithm_name=r[5],
                algorithm_version=r[6],
                params=json.loads(r[7]),
                input_handles=json.loads(r[8]),
                state=JobState(r[9]),
                progress_current=r[10],
                progress_total=r[11],
                fail_reason=JobFailReason(r[12]) if r[12] else None,
                fail_exit_code=r[13],
                fail_message=r[14],
                created_ts=r[15],
                updated_ts=r[16],
                reused_from_job_id=r[17],
                parent_job_id=r[18],
                shard_element_id=r[19],
                started_ts=r[20],
                expected_shards=r[21],
            )
            for r in rows
        ]

    async def list_recent(
        self,
        limit: int = 100,
        *,
        workflow_id: str | None = None,
        state: str | None = None,
        algorithm_name: str | None = None,
        order: str = "desc",
    ) -> list[dict[str, Any]]:
        """Return recent jobs as JSON-friendly dicts.

        Filters (all optional) are AND-composed. ``order`` is "desc"
        (newest first, default — matches the panel + Gallery) or "asc"
        (oldest first, useful for agents walking a workflow's history).

        ``state`` accepts an exact state string (``pending`` / ``running``
        / ``done`` / etc.) OR the alias ``"live"``, which expands to the
        union ``(pending, assigned, running, orphaned)`` — the same set
        the cancel-all endpoint targets. The alias exists because
        ``?state=running`` on its own misses ``assigned`` and
        ``orphaned`` rows, producing the "UI shows a running shard but
        ``?state=running`` returns 0" query-inconsistency operators
        hit during cancel storms.
        """

        clauses: list[str] = []
        params: list[Any] = []
        if workflow_id:
            clauses.append("workflow_id=?")
            params.append(workflow_id)
        if state == "live":
            clauses.append("state IN ('pending', 'assigned', 'running', 'orphaned')")
        elif state:
            clauses.append("state=?")
            params.append(state)
        if algorithm_name:
            clauses.append("algorithm_name=?")
            params.append(algorithm_name)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        direction = "ASC" if order.lower() == "asc" else "DESC"

        async with (
            self._db.read() as conn,
            conn.execute(
                f"""
                SELECT job_id, workflow_id, node_id, graph_node_id, algorithm_name,
                       algorithm_version, state, progress_current, progress_total,
                       fail_reason, created_ts, updated_ts, started_ts,
                       parent_job_id, shard_element_id, expected_shards
                FROM jobs
                {where}
                ORDER BY created_ts {direction}
                LIMIT ?
                """,
                (*params, limit),
            ) as cur,
        ):
            rows = await cur.fetchall()

        return [
            {
                "job_id": r[0],
                "workflow_id": r[1],
                "node_id": r[2],
                "graph_node_id": r[3],
                "algorithm_name": r[4],
                "algorithm_version": r[5],
                "state": r[6],
                "progress": {"current": r[7], "total": r[8]} if r[7] is not None else None,
                "fail_reason": r[9],
                "created_ts": r[10],
                "updated_ts": r[11],
                "started_ts": r[12],
                "parent_job_id": r[13],
                "shard_element_id": r[14],
                "expected_shards": r[15],
            }
            for r in rows
        ]

    async def list_by_snapshot(self, snapshot_id: str) -> list[dict[str, Any]]:
        """Every job belonging to one snapshot (i.e. one "run").

        Two selection sources are UNIONed:

        1. ``snapshot_jobs`` — the V8 lineage-first bridge that records
           which produced job satisfies each ``graph_node_id`` in this
           snapshot. Under the Continue/Fork model a job (including one
           first produced for an earlier snapshot) can attribute to any
           number of snapshots that share its output; a Fork that
           reuses upstream artifacts sees the original producing jobs
           verbatim (real state, real timestamps) — not the
           ``reused_from_job_id`` bookkeeping shells the pre-V8
           rerun-from path used to synthesise.

        2. ``jobs.snapshot_id`` filtered to non-done — the failed /
           cancelled / interrupted attempts made *against* this
           snapshot. These are not attributions (they produced no
           artifact) but they still belong in the run view so the user
           can see which steps failed.

        Duplicate rows (job_id present in both sources) are keyed on
        ``job_id`` and de-duplicated, preferring the bridge row for its
        ``graph_node_id`` value.

        Returned in job creation order.
        """

        async with self._db.read() as conn:
            async with conn.execute(
                """
                SELECT j.job_id, j.workflow_id, j.node_id, sj.graph_node_id,
                       j.algorithm_name, j.algorithm_version,
                       j.state, j.progress_current, j.progress_total,
                       j.fail_reason, j.fail_exit_code, j.fail_message,
                       j.params_json, j.input_handles_json,
                       j.created_ts, j.updated_ts,
                       j.reused_from_job_id, j.started_ts,
                       j.parent_job_id, j.shard_element_id, j.expected_shards
                FROM snapshot_jobs sj
                JOIN jobs j ON j.job_id = sj.job_id
                WHERE sj.snapshot_id = ?
                UNION ALL
                SELECT j.job_id, j.workflow_id, j.node_id, j.graph_node_id,
                       j.algorithm_name, j.algorithm_version,
                       j.state, j.progress_current, j.progress_total,
                       j.fail_reason, j.fail_exit_code, j.fail_message,
                       j.params_json, j.input_handles_json,
                       j.created_ts, j.updated_ts,
                       j.reused_from_job_id, j.started_ts,
                       j.parent_job_id, j.shard_element_id, j.expected_shards
                FROM jobs j
                WHERE j.snapshot_id = ?
                  AND j.state != 'done'
                  AND j.job_id NOT IN (
                      SELECT sj2.job_id
                      FROM snapshot_jobs sj2
                      WHERE sj2.snapshot_id = ?
                  )
                ORDER BY created_ts ASC
                """,
                (snapshot_id, snapshot_id, snapshot_id),
            ) as cur:
                rows = await cur.fetchall()

            # Dedup by job_id (a job attributed via the bridge AND also
            # having jobs.snapshot_id = this snapshot appears twice from
            # the UNION ALL — the bridge row wins by virtue of coming
            # first thanks to created_ts ordering being stable for the
            # same job).
            seen: set[str] = set()
            unique_rows = []
            for r in rows:
                if r[0] in seen:
                    continue
                seen.add(r[0])
                unique_rows.append(r)
            rows = unique_rows

            # For output-handle lookup: handles are keyed by the
            # *producing* job_id. Under the new bridge, snapshot_jobs
            # attributes the origin job directly, so a simple lookup by
            # each row's own job_id is correct — no reused_from_job_id
            # walk required. We keep the reused_from_job_id fallback
            # for defence in depth: any pre-V8 row that slipped through
            # backfill still resolves.
            lookup_ids: list[str] = []
            for r in rows:
                lookup_ids.append(r[0])
                if r[16] is not None:
                    lookup_ids.append(r[16])
            output_handles_by_id: dict[str, dict[str, str]] = {jid: {} for jid in lookup_ids}
            output_handle_states_by_id: dict[str, dict[str, str]] = {jid: {} for jid in lookup_ids}
            if lookup_ids:
                placeholders = ",".join("?" for _ in lookup_ids)
                async with conn.execute(
                    f"""
                    SELECT job_id, output_port_name, handle_id, deleted_ts
                    FROM handles
                    WHERE job_id IN ({placeholders})
                      AND output_port_name IS NOT NULL
                    """,
                    lookup_ids,
                ) as cur:
                    for h_job_id, port_name, handle_id, deleted_ts in await cur.fetchall():
                        output_handles_by_id[h_job_id][port_name] = handle_id
                        output_handle_states_by_id[h_job_id][port_name] = (
                            "deleted" if deleted_ts is not None else "pending"
                        )

        return [
            {
                "job_id": r[0],
                "workflow_id": r[1],
                "node_id": r[2],
                "graph_node_id": r[3],
                "algorithm_name": r[4],
                "algorithm_version": r[5],
                "state": r[6],
                "progress": {"current": r[7], "total": r[8]} if r[7] is not None else None,
                "fail_reason": r[9],
                "fail_exit_code": r[10],
                "fail_message": r[11],
                "params": json.loads(r[12]),
                "input_handles": json.loads(r[13]),
                "output_handles": (
                    output_handles_by_id.get(r[0])
                    or (output_handles_by_id.get(r[16]) if r[16] is not None else None)
                    or None
                ),
                "output_handle_states": (
                    output_handle_states_by_id.get(r[0])
                    or (output_handle_states_by_id.get(r[16]) if r[16] is not None else None)
                    or {}
                ),
                "reused_from_job_id": r[16],
                "started_ts": r[17],
                "parent_job_id": r[18],
                "shard_element_id": r[19],
                "expected_shards": r[20],
                "created_ts": r[14],
                "updated_ts": r[15],
            }
            for r in rows
        ]


# -- snapshot_jobs bridge: many-to-many attribution ---------------------------


class SnapshotJobsStore:
    """CRUD for the ``snapshot_jobs`` bridge table (V8 migration).

    Under the lineage-first snapshot model a snapshot is defined by the set
    of ``(graph_node_id, job_id)`` rows attributed to it here — not by the
    scalar ``jobs.snapshot_id`` column, which now records only the *first*
    snapshot a job appeared in (a useful indexing hint).

    Operations:

    * :meth:`attribute` — add one attribution row. Called on ``done`` for
      every job the runner completed; also called during Fork to inherit
      the parent snapshot's not-forked jobs into the new snapshot.
    * :meth:`get_job_at` — Continue/Fork dispatcher's key predicate: is a
      given graph_node_id already resolved in this snapshot? Returns the
      attributed job_id or None.
    * :meth:`list_attributions` — enumerate a snapshot's contents. Order
      of the underlying rowid is used as a deterministic tie-breaker for
      concurrent writes, not a semantic order.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def attribute(self, snapshot_id: str, job_id: str, graph_node_id: str) -> None:
        """Insert one attribution row. Idempotent (INSERT OR IGNORE)."""

        async def _write(conn: aiosqlite.Connection) -> None:
            await conn.execute(
                """
                INSERT OR IGNORE INTO snapshot_jobs
                    (snapshot_id, job_id, graph_node_id)
                VALUES (?, ?, ?)
                """,
                (snapshot_id, job_id, graph_node_id),
            )

        await self._db.write(_write)

    async def attribute_many(self, snapshot_id: str, rows: list[tuple[str, str]]) -> None:
        """Bulk-insert (job_id, graph_node_id) pairs into one snapshot.

        Used at Fork time to promote every "inherited" attribution from
        the parent snapshot into the child. Fast-path over a single
        transaction so a 100-node graph doesn't turn into 100 fsyncs.
        """

        if not rows:
            return

        async def _write(conn: aiosqlite.Connection) -> None:
            await conn.executemany(
                """
                INSERT OR IGNORE INTO snapshot_jobs
                    (snapshot_id, job_id, graph_node_id)
                VALUES (?, ?, ?)
                """,
                [(snapshot_id, job_id, gnode) for job_id, gnode in rows],
            )

        await self._db.write(_write)

    async def get_job_at(self, snapshot_id: str, graph_node_id: str) -> str | None:
        """Return the ``job_id`` attributed to ``graph_node_id`` in this
        snapshot, or ``None`` if that slot is empty.

        Used both by the Continue/Fork dispatcher (non-None → slot
        taken → Fork; None → Continue) and by upstream input resolution
        (looks up ``handles.list_by_job(job_id)`` to bind an edge's
        source handle to its concrete artifact).

        Fan-out safety: an arrayed<T> slot has one parent + N shard
        rows all attributed to the same (snapshot_id, graph_node_id).
        The parent produces the fanned-in arrayed<T> output; each
        shard produces only its per-element slice. Downstream input
        resolution needs the parent's job_id so
        ``handles.list_by_job`` returns the aggregated output, not a
        single shard's slice. We rank rows by
        ``jobs.parent_job_id IS NULL`` (parent first) with the newest
        parent winning ties by ``created_ts``.
        """

        async with (
            self._db.read() as conn,
            conn.execute(
                """
                SELECT sj.job_id
                FROM snapshot_jobs sj
                JOIN jobs j ON j.job_id = sj.job_id
                WHERE sj.snapshot_id = ? AND sj.graph_node_id = ?
                ORDER BY (j.parent_job_id IS NULL) DESC, j.created_ts DESC
                LIMIT 1
                """,
                (snapshot_id, graph_node_id),
            ) as cur,
        ):
            row = await cur.fetchone()
        return row[0] if row else None

    async def list_attributions(self, snapshot_id: str) -> list[dict[str, str]]:
        """Return ``[{job_id, graph_node_id}]`` for one snapshot."""

        async with (
            self._db.read() as conn,
            conn.execute(
                """
                SELECT job_id, graph_node_id
                FROM snapshot_jobs
                WHERE snapshot_id = ?
                ORDER BY rowid ASC
                """,
                (snapshot_id,),
            ) as cur,
        ):
            rows = await cur.fetchall()
        return [{"job_id": r[0], "graph_node_id": r[1]} for r in rows]

    async def get_latest_snapshot_for_workflow(self, workflow_id: str) -> str | None:
        """Return the newest snapshot id for the given workflow, or None.

        Used by the "dispatch single node" endpoint when the caller
        doesn't name an explicit base snapshot: the default base is the
        workflow's most recent snapshot (Continue/Fork onto it) so the
        UI's per-node "Run this" button doesn't need to remember which
        snapshot to extend.
        """

        async with (
            self._db.read() as conn,
            conn.execute(
                """
                SELECT snapshot_id FROM snapshots
                WHERE workflow_id = ?
                ORDER BY created_ts DESC
                LIMIT 1
                """,
                (workflow_id,),
            ) as cur,
        ):
            row = await cur.fetchone()
        return row[0] if row else None


# -- log store: append-only line log per job ---------------------------------


class LogStore:
    """Append-only stdout/stderr per job.

    Companion to :class:`JobsStore` — the frontend gets live lines over the
    WS ``log_chunk`` broadcast, but the ``/api/jobs/{id}/log`` REST endpoint
    reads from this table so agents can tail a completed run without holding
    a WebSocket open.

    Every appended line is one row; batched inserts share one transaction
    so a chatty algorithm doesn't turn into a flood of fsyncs.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def append(
        self,
        job_id: str,
        stream: str,
        lines: list[str],
        ts: float | None = None,
    ) -> None:
        """Append one batch of lines. ``ts`` defaults to now."""

        if not lines:
            return
        stamp = ts if ts is not None else time.time()

        async def _write(conn: aiosqlite.Connection) -> None:
            await conn.executemany(
                "INSERT INTO job_logs (job_id, stream, line, ts) VALUES (?, ?, ?, ?)",
                [(job_id, stream, line, stamp) for line in lines],
            )

        await self._db.write(_write)

    async def tail(
        self,
        job_id: str,
        *,
        n: int = 200,
        stream: str = "both",
    ) -> dict[str, Any]:
        """Return the last ``n`` lines for ``job_id`` in one of stdout/stderr/both.

        ``stream="both"`` interleaves in insertion order (matches wall-clock
        ordering of what the algorithm actually printed). Returns
        ``{"lines": [{"stream", "line", "ts"}], "total_returned", "truncated": bool}``.
        """

        stream = stream.lower()
        if stream not in ("stdout", "stderr", "both"):
            raise ValueError(f"stream must be stdout|stderr|both, got {stream!r}")
        if n <= 0:
            return {"lines": [], "total_returned": 0, "truncated": False}

        where = "job_id=?"
        params: list[Any] = [job_id]
        if stream != "both":
            where += " AND stream=?"
            params.append(stream)

        # Fetch the last N rows by autoincrement id, then flip so the caller
        # sees them in append order (oldest first, matching what a user would
        # see if they had watched stdout scroll live).
        async with (
            self._db.read() as conn,
            conn.execute(
                f"""
                SELECT stream, line, ts
                FROM (
                    SELECT id, stream, line, ts
                    FROM job_logs
                    WHERE {where}
                    ORDER BY id DESC
                    LIMIT ?
                ) AS recent
                ORDER BY id ASC
                """,
                (*params, n),
            ) as cur,
        ):
            rows = await cur.fetchall()

        # Count total to say "truncated" if the tail didn't cover everything.
        count_where = where
        async with (
            self._db.read() as conn,
            conn.execute(
                f"SELECT COUNT(*) FROM job_logs WHERE {count_where}",
                tuple(params),
            ) as cur,
        ):
            (total,) = await cur.fetchone()

        return {
            "lines": [{"stream": s, "line": ln, "ts": ts} for s, ln, ts in rows],
            "total_returned": len(rows),
            "truncated": total > len(rows),
        }
