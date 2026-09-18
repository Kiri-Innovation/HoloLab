"""Framework-managed arrayed<T> fan-out in ``execution.run_snapshot`` (M3).

Covers the sequential v1 slice:

* ``_discover_element_ids`` — the element list is the sorted subdir names of
  every arrayed input handle; disagreement between arrayed inputs raises.
* ``_shard_input_handles`` — arrayed inputs get a synthetic sub-handle
  pointing at their per-element subdirectory; scalar inputs pass through.
* ``rendered_output_paths`` shard override — a shard job's ``{{ outputs.X }}``
  redirects into ``{parent_ws}/{X}/{element_id}/`` so the parent's workspace
  naturally aggregates every element's subdir.
* End-to-end fan-out through ``run_snapshot`` with a stubbed node loop —
  parent job goes PENDING → RUNNING → DONE, N shard rows carry
  ``parent_job_id`` + ``shard_element_id``, and each shard's ``job_assign``
  frame carries the correct ``shard_output_prefix``.
* Failure propagation — a failing shard marks the parent FAILED and the
  scheduler raises ``WorkflowRunError`` without dispatching remaining shards.

Uses a MagicMock WebSocket to observe ``job_assign`` frames and a background
task that drives each shard through ASSIGNED → RUNNING → DONE|FAILED as they
appear in the jobs table.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from hololab.gateway.app import create_app
from hololab.gateway.execution import (
    WorkflowRunError,
    _discover_element_ids,
    _shard_input_handles,
    run_snapshot,
)
from hololab.gateway.handles import Handle, HandleBook
from hololab.persistence.db import open_database
from hololab.gateway.jobs import Job, JobState, JobStateMachine, event_from_transition
from hololab.gateway.registry import NodeSession
from hololab.gateway.workflows import (
    GraphEdge,
    GraphNode,
    WorkflowGraph,
)
from hololab.manifest.render import rendered_output_paths
from hololab.manifest.schema import Manifest
from hololab.protocol.messages import GpuInfo, JobFailReason


# ---------------------------------------------------------------------------
# Unit — rendered_output_paths shard override
# ---------------------------------------------------------------------------


def _simple_manifest(outputs: list[str]) -> Manifest:
    """A minimal Manifest built without going through disk."""
    return Manifest(
        apiVersion="hololab.dev/v1",
        kind="Algorithm",
        name="fake",
        version="0.1.0",
        inputs={},
        outputs={n: {"tags": ["x"]} for n in outputs},  # type: ignore[arg-type]
        params={},
        runtime={"env": "kiri"},  # type: ignore[arg-type]
        exec={"shell": "true"},  # type: ignore[arg-type]
    )


def test_rendered_output_paths_regular_job() -> None:
    m = _simple_manifest(["frames"])
    paths = rendered_output_paths(m, Path("/ws"), "wf1", "job1")
    assert paths == {"frames": "/ws/w/wf1/j/job1/frames"}


def test_rendered_output_paths_shard_redirects_into_parent_workspace() -> None:
    m = _simple_manifest(["frames"])
    paths = rendered_output_paths(
        m,
        Path("/ws"),
        "wf1",
        "shard-abc",
        shard_output_prefix="/ws/w/wf1/j/parent-xyz",
        shard_element_id="cam_A",
    )
    assert paths == {"frames": "/ws/w/wf1/j/parent-xyz/frames/cam_A"}


# ---------------------------------------------------------------------------
# Unit — _discover_element_ids + _shard_input_handles
# ---------------------------------------------------------------------------


async def _seeded_book(tmp_path: Path, *, subdirs: list[str]) -> tuple[HandleBook, Handle]:
    """Register one arrayed handle whose directory contains ``subdirs``."""
    db = await open_database(tmp_path / "db.sqlite")
    book = HandleBook(db)
    root = tmp_path / "arr"
    root.mkdir()
    for name in subdirs:
        (root / name).mkdir()
    h = Handle(
        handle_id="h-arr",
        node_id="node",
        storage="dir",
        tags=["frame_sequence"],
        path=str(root),
        job_id=None,
        output_port_name=None,
    )
    await book.register(h)
    return book, h


@pytest.mark.asyncio
async def test_discover_element_ids_returns_sorted_subdirs(tmp_path: Path) -> None:
    book, h = await _seeded_book(tmp_path, subdirs=["cam_C", "cam_A", "cam_B"])
    elements = await _discover_element_ids(
        handles=book,
        input_handles={"frames": h.handle_id},
        arrayed_input_ports=["frames"],
    )
    assert elements == ["cam_A", "cam_B", "cam_C"]


@pytest.mark.asyncio
async def test_discover_element_ids_rejects_mismatched_sets(tmp_path: Path) -> None:
    # Two arrayed inputs on the same node with disagreeing element sets.
    db = await open_database(tmp_path / "db.sqlite")
    book = HandleBook(db)
    r1 = tmp_path / "a"
    r1.mkdir()
    (r1 / "cam_A").mkdir()
    (r1 / "cam_B").mkdir()
    r2 = tmp_path / "b"
    r2.mkdir()
    (r2 / "cam_A").mkdir()
    (r2 / "cam_Z").mkdir()  # ← mismatched
    h1 = Handle(handle_id="h1", node_id="n", storage="dir", tags=["t"], path=str(r1))
    h2 = Handle(handle_id="h2", node_id="n", storage="dir", tags=["t"], path=str(r2))
    await book.register(h1)
    await book.register(h2)
    with pytest.raises(WorkflowRunError, match="disagree on element set"):
        await _discover_element_ids(
            handles=book,
            input_handles={"a": "h1", "b": "h2"},
            arrayed_input_ports=["a", "b"],
        )


@pytest.mark.asyncio
async def test_shard_input_handles_creates_synthetic_subhandles(tmp_path: Path) -> None:
    book, h = await _seeded_book(tmp_path, subdirs=["cam_A", "cam_B"])
    shard_inputs = await _shard_input_handles(
        handles=book,
        input_handles={"frames": h.handle_id, "config": "h-scalar"},
        arrayed_input_ports=["frames"],
        element_id="cam_A",
        producer_node_id="node",
    )
    assert shard_inputs["config"] == "h-scalar"  # scalar passes through
    sub_id = shard_inputs["frames"]
    assert sub_id != h.handle_id  # arrayed got a fresh synthetic handle
    sub = await book.get(sub_id)
    assert sub is not None
    assert sub.path == str(Path(h.path) / "cam_A")
    assert sub.tags == h.tags  # inherits parent tags
    assert sub.job_id is None  # transient bookkeeping, not tied to a job


# ---------------------------------------------------------------------------
# End-to-end — run_snapshot fan-out through a stubbed node loop
# ---------------------------------------------------------------------------


def _fake_online_node(
    client_app: Any, *, workspace_root: Path, node_id: str = "node-a"
) -> NodeSession:
    ws = MagicMock()
    ws.send_text = AsyncMock()
    session = NodeSession(
        node_id=node_id,
        session_id="sess",
        node_name="node-a-name",
        ws=ws,
        advertised_url=None,
        gpu=GpuInfo(),
        packs=[],
        protocol_v=1,
        workspace_root=str(workspace_root),
    )
    client_app.state.registry._sessions[session.node_id] = session
    return session


def _install_fake_catalog(client_app: Any, *, arrayable: bool = True) -> None:
    """Stub ``registry.catalog_json`` so we don't need real pack files on disk."""
    entry = {
        "name": "fanout-demo",
        "version": "0.1.0",
        "manifest_hash": "x" * 64,
        "node_ids": ["node-a"],
        "description": None,
        "category": [],
        "docs": None,
        "source_entry": None,
        "manifest_path": None,
        "source_dir": None,
        "arrayable": arrayable,
        "inputs": {
            "frames": {
                "tags": ["frame_sequence"],
                "required": True,
                "storage": "dir",
                "description": None,
                "arrayed": False,
            }
        },
        "outputs": {
            "out": {
                "tags": ["frame_sequence"],
                "storage": "dir",
                "description": None,
                "preview": None,
                "arrayed": False,
                "tags_from": None,
            }
        },
        "params": {},
    }
    client_app.state.registry.catalog_json = lambda: [entry]


async def _drive_shards(
    client_app: Any,
    *,
    fail_at_index: int | None = None,
) -> asyncio.Task[None]:
    """Poll for shard jobs and transition each through ASSIGNED→RUNNING→DONE.

    Drives sequentially in the order shards appear in the jobs table so the
    scheduler's ``_await_job_terminal`` unblocks. Returns the background
    task so tests can await its completion.
    """

    store = client_app.state.jobs_store
    hub = client_app.state.hub
    processed: set[str] = set()

    async def _loop() -> None:
        # Bounded so a test bug doesn't hang forever.
        for _ in range(200):
            rows = await store.list_recent(limit=200, order="asc")
            for r in rows:
                jid = r["job_id"]
                if jid in processed:
                    continue
                if r.get("state") != "assigned":
                    continue
                job = await store.get(jid)
                if job is None or job.parent_job_id is None:
                    # Only touch shard jobs — the parent is coordinator-only
                    # and is driven by _run_fanout_node itself.
                    continue
                # ASSIGNED → RUNNING
                running = JobStateMachine.transition(job, JobState.RUNNING)
                kind, payload = event_from_transition(job, running)
                await store.update(running, kind, payload)
                idx_seen = len(processed)
                processed.add(jid)
                should_fail = (
                    fail_at_index is not None and idx_seen == fail_at_index
                )
                if should_fail:
                    failed = JobStateMachine.transition(
                        running,
                        JobState.FAILED,
                        fail_reason=JobFailReason.USER_ERROR,
                        fail_message="stubbed failure",
                    )
                    kind, payload = event_from_transition(running, failed)
                    await store.update(failed, kind, payload)
                    return
                done = JobStateMachine.transition(running, JobState.DONE)
                kind, payload = event_from_transition(running, done)
                await store.update(done, kind, payload)
            await asyncio.sleep(0.01)

    return asyncio.create_task(_loop())


@pytest.mark.asyncio
async def test_fanout_creates_shards_and_registers_aggregate(tmp_path: Path) -> None:
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    app = create_app(db_path=tmp_path / "test.sqlite")

    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        session = _fake_online_node(app, workspace_root=ws_root)
        _install_fake_catalog(app)

        # Arrayed input on disk — one subdir per element.
        arr_root = tmp_path / "arr"
        arr_root.mkdir()
        (arr_root / "cam_A").mkdir()
        (arr_root / "cam_B").mkdir()

        async def _seed() -> str:
            # Register the arrayed input handle.
            await app.state.handles.register(
                Handle(
                    handle_id="h-arr",
                    node_id="node-a",
                    storage="dir",
                    tags=["frame_sequence"],
                    path=str(arr_root),
                )
            )
            # Create a fake upstream job whose output handle is h-arr so
            # _wire_inputs can find it. graph_node_id="src".
            upstream = Job(
                job_id="upstream-job",
                workflow_id="wf1",
                snapshot_id=None,
                algorithm_name="src",
                algorithm_version="0.1.0",
                params={},
                input_handles={},
                graph_node_id="src",
                state=JobState.DONE,
            )
            await app.state.jobs_store.create(upstream)
            # Re-register the handle attributed to upstream so
            # _collect_output_handles can find it — HandleBook.register is
            # idempotent (INSERT OR REPLACE), so we just re-register with
            # job_id + output_port_name populated.
            await app.state.handles.register(
                Handle(
                    handle_id="h-arr",
                    node_id="node-a",
                    storage="dir",
                    tags=["frame_sequence"],
                    path=str(arr_root),
                    job_id="upstream-job",
                    output_port_name="out",
                )
            )
            # Snapshot with A (upstream) → B (fan-out).
            graph = WorkflowGraph(
                nodes=[
                    GraphNode(
                        id="src",
                        algorithm_name="src",
                        algorithm_version="0.1.0",
                        assigned_node_id="node-a",
                    ),
                    GraphNode(
                        id="fan",
                        algorithm_name="fanout-demo",
                        algorithm_version="0.1.0",
                        assigned_node_id="node-a",
                        arrayed_toggle=True,
                    ),
                ],
                edges=[
                    GraphEdge(
                        id="e",
                        source="src",
                        sourceHandle="out",
                        target="fan",
                        targetHandle="frames",
                    ),
                ],
            )
            await app.state.workflows.save_draft(
                workflow_id="wf1", name="fan-e2e", graph=graph
            )
            snap = await app.state.workflows.create_snapshot(
                workflow_id="wf1", graph=graph
            )
            return snap.snapshot_id, graph

        snapshot_id, graph = client.portal.call(_seed)

        async def _drive_and_run() -> list[str]:
            driver_task = await _drive_shards(app)
            try:
                return await run_snapshot(
                    app,
                    snapshot_id=snapshot_id,
                    graph=graph,
                    workflow_id="wf1",
                    skip_graph_nodes={"src"},
                    seed_outputs={"src": {"out": "h-arr"}},
                )
            finally:
                driver_task.cancel()

        job_ids = client.portal.call(_drive_and_run)

        # Parent job returned + 2 shards persisted with parent_job_id.
        assert len(job_ids) == 1
        parent_id = job_ids[0]
        shards = client.portal.call(
            lambda: app.state.jobs_store.list_shards_of(parent_id)
        )
        assert len(shards) == 2
        assert sorted(s.shard_element_id for s in shards) == ["cam_A", "cam_B"]
        for s in shards:
            assert s.state is JobState.DONE
            assert s.parent_job_id == parent_id

        # Aggregate output handle registered on the parent, pointing at
        # ``{parent_ws}/out`` — the natural aggregation directory.
        parent_handles = client.portal.call(
            lambda: app.state.handles.list_by_job(parent_id)
        )
        assert len(parent_handles) == 1
        aggregate = parent_handles[0]
        assert aggregate.output_port_name == "out"
        assert aggregate.path == str(ws_root / "w" / "wf1" / "j" / parent_id / "out")
        assert aggregate.tags == ["frame_sequence"]

        # Every shard's job_assign frame carried shard_element_id +
        # shard_output_prefix.
        frames_sent = [call.args[0] for call in session.ws.send_text.call_args_list]
        assign_frames = [
            json.loads(f) for f in frames_sent if '"kind":"job_assign"' in f
        ]
        assert len(assign_frames) == 2
        payloads = sorted(
            (env["payload"] for env in assign_frames),
            key=lambda p: p["shard_element_id"],
        )
        assert payloads[0]["shard_element_id"] == "cam_A"
        assert payloads[1]["shard_element_id"] == "cam_B"
        for p in payloads:
            assert p["shard_output_prefix"] == str(
                ws_root / "w" / "wf1" / "j" / parent_id
            )


@pytest.mark.asyncio
async def test_fanout_shard_failure_marks_parent_failed(tmp_path: Path) -> None:
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    app = create_app(db_path=tmp_path / "test.sqlite")

    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        _fake_online_node(app, workspace_root=ws_root)
        _install_fake_catalog(app)
        arr_root = tmp_path / "arr"
        arr_root.mkdir()
        (arr_root / "cam_A").mkdir()
        (arr_root / "cam_B").mkdir()
        (arr_root / "cam_C").mkdir()

        async def _seed() -> tuple[str, WorkflowGraph]:
            await app.state.handles.register(
                Handle(
                    handle_id="h-arr",
                    node_id="node-a",
                    storage="dir",
                    tags=["frame_sequence"],
                    path=str(arr_root),
                    job_id="upstream-job",
                    output_port_name="out",
                )
            )
            upstream = Job(
                job_id="upstream-job",
                workflow_id="wf1",
                snapshot_id=None,
                algorithm_name="src",
                algorithm_version="0.1.0",
                params={},
                input_handles={},
                graph_node_id="src",
                state=JobState.DONE,
            )
            await app.state.jobs_store.create(upstream)
            graph = WorkflowGraph(
                nodes=[
                    GraphNode(
                        id="src",
                        algorithm_name="src",
                        algorithm_version="0.1.0",
                        assigned_node_id="node-a",
                    ),
                    GraphNode(
                        id="fan",
                        algorithm_name="fanout-demo",
                        algorithm_version="0.1.0",
                        assigned_node_id="node-a",
                        arrayed_toggle=True,
                    ),
                ],
                edges=[
                    GraphEdge(
                        id="e",
                        source="src",
                        sourceHandle="out",
                        target="fan",
                        targetHandle="frames",
                    ),
                ],
            )
            await app.state.workflows.save_draft(
                workflow_id="wf1", name="fan-fail-e2e", graph=graph
            )
            snap = await app.state.workflows.create_snapshot(
                workflow_id="wf1", graph=graph
            )
            return snap.snapshot_id, graph

        snapshot_id, graph = client.portal.call(_seed)

        # Fail the 2nd shard (index 1 = cam_B).
        async def _drive_and_run() -> list[str]:
            driver_task = await _drive_shards(app, fail_at_index=1)
            try:
                return await run_snapshot(
                    app,
                    snapshot_id=snapshot_id,
                    graph=graph,
                    workflow_id="wf1",
                    skip_graph_nodes={"src"},
                    seed_outputs={"src": {"out": "h-arr"}},
                )
            finally:
                driver_task.cancel()

        with pytest.raises(WorkflowRunError, match="shard 1"):
            client.portal.call(_drive_and_run)

        # Only 2 shards should have been dispatched (0=cam_A done, 1=cam_B
        # failed) — the third element is never created.
        rows = client.portal.call(
            lambda: app.state.jobs_store.list_recent(100, order="asc")
        )
        shard_rows = [r for r in rows if r["graph_node_id"] == "fan"]
        # 1 parent + 2 shards. Third (cam_C) skipped.
        assert len(shard_rows) == 3
