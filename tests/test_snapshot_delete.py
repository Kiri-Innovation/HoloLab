"""Ref-counted snapshot deletion — the "delete run + artifacts" flow.

The keystone invariant these tests defend:

  An artifact H produced by job J is physically deleted iff dropping the
  target snapshot S leaves J with **zero** remaining ``snapshot_jobs``
  attributions. If any other snapshot still references J, H stays on
  disk and its handle row keeps ``deleted_ts=NULL``.

This is the "artifact stays if shared" rule the operator asked for on
the Run History right-click menu.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from hololab.gateway.app import create_app
from hololab.gateway.handles import Handle
from hololab.gateway.jobs import Job, JobState
from hololab.gateway.registry import NodeSession
from hololab.gateway.workflows import GraphNode, WorkflowGraph
from hololab.protocol.messages import GpuInfo


def _one_node_graph() -> WorkflowGraph:
    """Single-node graph: ``A``. Enough to prove the ref-count rule."""

    return WorkflowGraph(
        nodes=[
            GraphNode(
                id="A",
                algorithm_name="demo-echo",
                algorithm_version="0.1.0",
                assigned_node_id="node-a",
            ),
        ],
        edges=[],
    )


def _fake_online_node(client: TestClient) -> list[dict[str, Any]]:
    """Register a fake online compute node.

    Returns a shared ``recorded`` list that captures every WS frame the
    gateway would have sent; also arranges for ``artifact_delete_req``
    frames to be auto-acked so ``delete_artifact`` returns cleanly with
    the ``freed_bytes`` we choose per handle.
    """

    recorded: list[dict[str, Any]] = []
    ws = MagicMock()

    async def _fake_send(text: str) -> None:
        import json

        frame = json.loads(text)
        recorded.append(frame)
        kind = frame.get("kind")
        payload = frame.get("payload") or {}
        # Auto-ack artifact_delete_req: resolve the pending future the
        # gateway installed keyed by ``payload.req_id``.
        if kind == "artifact_delete_req":
            req_id = payload["req_id"]
            fut = client.app.state.pending_artifact_deletes.get(req_id)
            if fut is not None and not fut.done():
                fut.set_result(
                    {"req_id": req_id, "ok": True, "freed_bytes": 1024}
                )

    ws.send_text = AsyncMock(side_effect=_fake_send)
    session = NodeSession(
        node_id="node-a",
        session_id="sess",
        node_name="node-a-name",
        ws=ws,
        advertised_url=None,
        gpu=GpuInfo(),
        packs=[],
        protocol_v=1,
    )
    client.app.state.registry._sessions[session.node_id] = session
    return recorded


def _seed_shared_artifact(
    client: TestClient, workflow_id: str
) -> tuple[str, str, str]:
    """Create two snapshots that BOTH attribute the same producing job.

    Returns ``(snap_a, snap_b, handle_id)``. Mimics a fork that reused
    the same upstream artifact — the canonical "shared artifact" case.
    ``portal.call`` doesn't take kwargs, so this wraps a positional-only
    async closure.
    """

    graph = _one_node_graph()

    async def _seed() -> tuple[str, str, str]:
        await client.app.state.workflows.save_draft(
            workflow_id=workflow_id, name="cc-e2e-refcount", graph=graph
        )
        snap_a = await client.app.state.workflows.create_snapshot(
            workflow_id=workflow_id, graph=graph
        )
        snap_b = await client.app.state.workflows.create_snapshot(
            workflow_id=workflow_id,
            graph=graph,
            parent_snapshot_id=snap_a.snapshot_id,
        )

        job = Job(
            job_id="job-shared",
            snapshot_id=snap_a.snapshot_id,  # first snapshot the job appeared in
            workflow_id=workflow_id,
            node_id="node-a",
            graph_node_id="A",
            algorithm_name="demo-echo",
            algorithm_version="0.1.0",
            params={},
            input_handles={},
            state=JobState.DONE,
        )
        await client.app.state.jobs_store.create(job)
        # ``jobs_store.create`` already writes the snap_a attribution
        # because the job is created done + has snapshot_id + graph_node_id.
        # Add the second attribution manually — this is the "fork reused
        # A's output" scenario the V8 model captures via ``snapshot_jobs``.
        await client.app.state.snapshot_jobs.attribute(
            snap_b.snapshot_id, job.job_id, "A"
        )

        await client.app.state.handles.register(
            Handle(
                handle_id="h-shared",
                node_id="node-a",
                storage="file",
                tags=["demo-echo"],
                path="/tmp/A.out",
                size_bytes=1024,
                job_id=job.job_id,
                output_port_name="out",
            )
        )
        return snap_a.snapshot_id, snap_b.snapshot_id, "h-shared"

    return client.portal.call(_seed)


# ---------------------------------------------------------------------------
# The core rule: exclusive vs shared
# ---------------------------------------------------------------------------


def test_preview_reports_shared_artifact_as_kept(tmp_path: Path) -> None:
    """A shared artifact is counted as ``shared_count`` (would-be kept),
    not ``exclusive_count`` (would-be deleted)."""

    app = create_app(db_path=tmp_path / "preview.sqlite")
    with TestClient(app) as client:
        _fake_online_node(client)
        wid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        snap_a, snap_b, _ = _seed_shared_artifact(client, wid)

        r = client.get(f"/api/snapshots/{snap_a}/deletion-preview")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["snapshot_id"] == snap_a
        assert body["blocked"] is False
        assert body["artifacts"]["exclusive_count"] == 0
        assert body["artifacts"]["shared_count"] == 1
        assert body["artifacts"]["exclusive_bytes"] == 0
        assert body["jobs"]["exclusive_count"] == 0
        assert body["jobs"]["shared_count"] == 1
        assert snap_b  # only used for the seed


def test_delete_shared_artifact_keeps_file_on_disk(tmp_path: Path) -> None:
    """The critical case: two snapshots reference the same artifact.
    Deleting the first must NOT physically delete the file, must NOT
    tombstone the handle row, and the second snapshot's preview must
    remain functional afterwards."""

    app = create_app(db_path=tmp_path / "shared.sqlite")
    with TestClient(app) as client:
        recorded = _fake_online_node(client)
        wid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        snap_a, snap_b, handle_id = _seed_shared_artifact(client, wid)

        r = client.delete(f"/api/snapshots/{snap_a}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["state"] == "deleted"
        assert body["artifacts_removed_from_disk"] == 0
        assert body["artifacts_kept_shared"] == 1
        assert body["jobs_removed"] == 0
        assert body["jobs_kept_shared"] == 1

        # No artifact_delete_req frame was ever sent to the node — the
        # ref-count check short-circuited the physical delete.
        assert not any(f["kind"] == "artifact_delete_req" for f in recorded)

        # Handle row survives with deleted_ts still NULL.
        h_row = client.get(f"/api/handles/{handle_id}").json()
        assert h_row["deleted_ts"] is None

        # Second snapshot still functional: preview endpoint lists the
        # artifact as its own exclusive one now (only reference left).
        r2 = client.get(f"/api/snapshots/{snap_b}/deletion-preview")
        assert r2.status_code == 200
        preview_b = r2.json()
        assert preview_b["artifacts"]["exclusive_count"] == 1
        assert preview_b["artifacts"]["shared_count"] == 0


def test_delete_last_reference_removes_file_and_tombstones(
    tmp_path: Path,
) -> None:
    """Follow-up to the above: after deleting snap_a, deleting snap_b
    now DOES physically remove the artifact (ref count went to zero)
    and stamps ``deleted_ts`` on the handle row."""

    app = create_app(db_path=tmp_path / "lastref.sqlite")
    with TestClient(app) as client:
        recorded = _fake_online_node(client)
        wid = "cccccccc-cccc-cccc-cccc-cccccccccccc"
        snap_a, snap_b, handle_id = _seed_shared_artifact(client, wid)

        # Drop the first snapshot; artifact untouched.
        client.delete(f"/api/snapshots/{snap_a}")

        # Now delete the second, last remaining reference.
        r = client.delete(f"/api/snapshots/{snap_b}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["artifacts_removed_from_disk"] == 1
        assert body["artifacts_kept_shared"] == 0
        assert body["jobs_removed"] == 1
        assert body["freed_bytes"] == 1024

        # An artifact_delete_req WAS sent to the node this time.
        delete_frames = [
            f for f in recorded if f["kind"] == "artifact_delete_req"
        ]
        assert len(delete_frames) == 1
        assert delete_frames[0]["payload"]["handle_id"] == handle_id

        # Handle row still exists (history-preserving) but is tombstoned.
        h_row = client.get(f"/api/handles/{handle_id}").json()
        assert h_row["deleted_ts"] is not None


def test_delete_offline_node_still_tombstones(tmp_path: Path) -> None:
    """When the producer node is offline we can't rm the bytes, but the
    delete still succeeds: handle is tombstoned, snapshot is gone."""

    app = create_app(db_path=tmp_path / "offline.sqlite")
    with TestClient(app) as client:
        # Do NOT register a NodeSession — the producer is offline.
        wid = "dddddddd-dddd-dddd-dddd-dddddddddddd"
        graph = _one_node_graph()

        async def _seed() -> str:
            await client.app.state.workflows.save_draft(
                workflow_id=wid, name="cc-offline", graph=graph
            )
            snap = await client.app.state.workflows.create_snapshot(
                workflow_id=wid, graph=graph
            )
            job = Job(
                job_id="job-off",
                snapshot_id=snap.snapshot_id,
                workflow_id=wid,
                node_id="node-a",
                graph_node_id="A",
                algorithm_name="demo-echo",
                algorithm_version="0.1.0",
                params={},
                input_handles={},
                state=JobState.DONE,
            )
            await client.app.state.jobs_store.create(job)
            await client.app.state.handles.register(
                Handle(
                    handle_id="h-off",
                    node_id="node-a",
                    storage="file",
                    tags=["demo-echo"],
                    path="/tmp/off.out",
                    size_bytes=99,
                    job_id=job.job_id,
                    output_port_name="out",
                )
            )
            return snap.snapshot_id

        snap_id = client.portal.call(_seed)

        r = client.delete(f"/api/snapshots/{snap_id}")
        assert r.status_code == 200
        body = r.json()
        assert body["artifacts_removed_from_disk"] == 0
        assert body["artifacts_tombstoned_only"] == 1
        assert body["freed_bytes"] == 0

        h_row = client.get("/api/handles/h-off").json()
        assert h_row["deleted_ts"] is not None


# ---------------------------------------------------------------------------
# Refusal + idempotency edges
# ---------------------------------------------------------------------------


def test_running_job_blocks_delete_with_409(tmp_path: Path) -> None:
    """A snapshot with a non-terminal (``running``) job is un-deletable
    until the operator cancels the run."""

    app = create_app(db_path=tmp_path / "live.sqlite")
    with TestClient(app) as client:
        _fake_online_node(client)
        wid = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
        graph = _one_node_graph()

        async def _seed() -> str:
            await client.app.state.workflows.save_draft(
                workflow_id=wid, name="cc-live", graph=graph
            )
            snap = await client.app.state.workflows.create_snapshot(
                workflow_id=wid, graph=graph
            )
            job = Job(
                job_id="job-run",
                snapshot_id=snap.snapshot_id,
                workflow_id=wid,
                node_id="node-a",
                graph_node_id="A",
                algorithm_name="demo-echo",
                algorithm_version="0.1.0",
                params={},
                input_handles={},
                state=JobState.RUNNING,
            )
            await client.app.state.jobs_store.create(job)
            return snap.snapshot_id

        snap_id = client.portal.call(_seed)

        preview = client.get(f"/api/snapshots/{snap_id}/deletion-preview").json()
        assert preview["blocked"] is True
        assert len(preview["live_jobs"]) == 1
        assert preview["live_jobs"][0]["state"] == "running"

        r = client.delete(f"/api/snapshots/{snap_id}")
        assert r.status_code == 409
        # ``detail`` is a dict with the live_jobs list embedded so the
        # frontend can render "these jobs are still running" without a
        # second round-trip.
        detail = r.json()["detail"]
        assert "live_jobs" in detail
        assert len(detail["live_jobs"]) == 1


def test_delete_unknown_snapshot_is_idempotent(tmp_path: Path) -> None:
    """Two tabs racing on the same delete: the second click sees ``gone``,
    not an error. The frontend just refreshes its list."""

    app = create_app(db_path=tmp_path / "gone.sqlite")
    with TestClient(app) as client:
        r = client.delete("/api/snapshots/00000000-0000-0000-0000-000000000000")
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "gone"


def test_preview_unknown_snapshot_is_404(tmp_path: Path) -> None:
    """Preview is a read — unknown snapshot is a genuine 404 so a UI
    that races between list-refresh and preview-open doesn't confuse an
    empty preview with 'exists but empty'."""

    app = create_app(db_path=tmp_path / "prev404.sqlite")
    with TestClient(app) as client:
        r = client.get(
            "/api/snapshots/00000000-0000-0000-0000-000000000000/deletion-preview"
        )
        assert r.status_code == 404


def test_delete_nulls_children_parent_pointer(tmp_path: Path) -> None:
    """Deleting the parent of a forked snapshot leaves the child alive
    with ``parent_snapshot_id`` nulled — the UI shouldn't render
    'forked from <deleted-uuid>' dead links."""

    app = create_app(db_path=tmp_path / "children.sqlite")
    with TestClient(app) as client:
        _fake_online_node(client)
        wid = "ffffffff-ffff-ffff-ffff-ffffffffffff"

        async def _seed() -> tuple[str, str]:
            graph = _one_node_graph()
            await client.app.state.workflows.save_draft(
                workflow_id=wid, name="cc-children", graph=graph
            )
            parent = await client.app.state.workflows.create_snapshot(
                workflow_id=wid, graph=graph
            )
            child = await client.app.state.workflows.create_snapshot(
                workflow_id=wid,
                graph=graph,
                parent_snapshot_id=parent.snapshot_id,
            )
            return parent.snapshot_id, child.snapshot_id

        parent_id, child_id = client.portal.call(_seed)

        r = client.delete(f"/api/snapshots/{parent_id}")
        assert r.status_code == 200

        # Child still exists; its parent pointer is nulled. Poke the
        # workflows store directly since there's no HTTP surface for
        # parent_snapshot_id yet.
        async def _read_child() -> str | None:
            row = await client.app.state.workflows.get_snapshot(child_id)
            return row.parent_snapshot_id if row is not None else "MISSING"

        assert client.portal.call(_read_child) is None
