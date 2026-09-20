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
from hololab.gateway.jobs import Job, JobState, JobStateMachine, event_from_transition
from hololab.gateway.registry import NodeSession
from hololab.gateway.workflows import (
    GraphEdge,
    GraphNode,
    WorkflowGraph,
)
from hololab.manifest.render import rendered_output_paths
from hololab.manifest.schema import Manifest
from hololab.persistence.db import open_database
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
async def test_discover_element_ids_skips_dotfiles_and_files(tmp_path: Path) -> None:
    """Framework sidecars (``.hololab-done``, ``.hololab-metadata.json``) and
    stray files must NOT be dispatched as shards — element = subdir only."""
    book, h = await _seeded_book(tmp_path, subdirs=["cam00", "cam01"])
    # Drop in the exact files a real producer's output would carry.
    (Path(h.path) / ".hololab-done").touch()
    (Path(h.path) / ".hololab-metadata.json").write_text("{}")
    (Path(h.path) / "stray.txt").write_text("noise")
    elements = await _discover_element_ids(
        handles=book,
        input_handles={"frames": h.handle_id},
        arrayed_input_ports=["frames"],
    )
    assert elements == ["cam00", "cam01"]


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
    with pytest.raises(WorkflowRunError, match="disagree on element set") as ei:
        await _discover_element_ids(
            handles=book,
            input_handles={"a": "h1", "b": "h2"},
            arrayed_input_ports=["a", "b"],
        )
    msg = str(ei.value)
    # New error message shape (post image-undistort spec): must list both
    # cardinalities AND surface the specific missing element on each side,
    # not just dump the full lists.
    assert "'a' has 2 element(s)" in msg, msg
    assert "'b' has 2 element(s)" in msg, msg
    assert "only in 'a': ['cam_B']" in msg, msg
    assert "only in 'b': ['cam_Z']" in msg, msg


@pytest.mark.asyncio
async def test_discover_element_ids_length_mismatch_readable(tmp_path: Path) -> None:
    """N=21 vs N=20 (the exact spec case for image-undistort's paired inputs) —
    the error must lead with counts + only-in-A/only-in-B, not dump 41 names."""
    db = await open_database(tmp_path / "db.sqlite")
    book = HandleBook(db)
    r1 = tmp_path / "cams"
    r1.mkdir()
    for i in range(21):
        (r1 / f"cam_{i:02d}").mkdir()
    r2 = tmp_path / "imgs"
    r2.mkdir()
    for i in range(20):  # one short
        (r2 / f"cam_{i:02d}").mkdir()
    h1 = Handle(handle_id="hc", node_id="n", storage="dir", tags=["t"], path=str(r1))
    h2 = Handle(handle_id="hi", node_id="n", storage="dir", tags=["t"], path=str(r2))
    await book.register(h1)
    await book.register(h2)
    with pytest.raises(WorkflowRunError) as ei:
        await _discover_element_ids(
            handles=book,
            input_handles={"cams": "hc", "images": "hi"},
            arrayed_input_ports=["cams", "images"],
        )
    msg = str(ei.value)
    assert "'cams' has 21 element(s)" in msg
    assert "'images' has 20 element(s)" in msg
    assert "only in 'cams': ['cam_20']" in msg
    # 20 elements agree — none only-in-images.
    assert "only in 'images'" not in msg


@pytest.mark.asyncio
async def test_discover_element_ids_diff_caps_large_lists(tmp_path: Path) -> None:
    """20-element diff on one side gets capped at 5 with a `(+N more)` tail."""
    db = await open_database(tmp_path / "db.sqlite")
    book = HandleBook(db)
    r1 = tmp_path / "a"
    r1.mkdir()
    for i in range(30):
        (r1 / f"e{i:03d}").mkdir()
    r2 = tmp_path / "b"
    r2.mkdir()
    for i in range(10):
        (r2 / f"e{i:03d}").mkdir()
    h1 = Handle(handle_id="hh1", node_id="n", storage="dir", tags=["t"], path=str(r1))
    h2 = Handle(handle_id="hh2", node_id="n", storage="dir", tags=["t"], path=str(r2))
    await book.register(h1)
    await book.register(h2)
    with pytest.raises(WorkflowRunError) as ei:
        await _discover_element_ids(
            handles=book,
            input_handles={"a": "hh1", "b": "hh2"},
            arrayed_input_ports=["a", "b"],
        )
    msg = str(ei.value)
    assert "'a' has 30 element(s)" in msg
    assert "'b' has 10 element(s)" in msg
    # 20 elements only-in-a; cap=5 → "(+15 more)"
    assert "(+15 more)" in msg


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

    Drives in the order shards appear in the jobs table so the scheduler's
    ``_await_job_terminal`` calls unblock. When ``fail_at_index`` is set,
    only that shard is marked FAILED — every other shard is still driven
    to DONE (matches the no-fail-fast gather-all executor semantics; the
    parent is failed by the executor once every shard is terminal).
    Returns the background task so tests can await its completion.
    """

    store = client_app.state.jobs_store
    processed: set[str] = set()

    async def _loop() -> None:
        # Bounded so a test bug doesn't hang forever.
        for _ in range(400):
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
                should_fail = fail_at_index is not None and idx_seen == fail_at_index
                if should_fail:
                    failed = JobStateMachine.transition(
                        running,
                        JobState.FAILED,
                        fail_reason=JobFailReason.USER_ERROR,
                        fail_message="stubbed failure",
                    )
                    kind, payload = event_from_transition(running, failed)
                    await store.update(failed, kind, payload)
                    continue  # keep driving remaining shards — no fail-fast
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
            await app.state.workflows.save_draft(workflow_id="wf1", name="fan-e2e", graph=graph)
            snap = await app.state.workflows.create_snapshot(workflow_id="wf1", graph=graph)
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
        shards = client.portal.call(lambda: app.state.jobs_store.list_shards_of(parent_id))
        assert len(shards) == 2
        assert sorted(s.shard_element_id for s in shards) == ["cam_A", "cam_B"]
        for s in shards:
            assert s.state is JobState.DONE
            assert s.parent_job_id == parent_id

        # Aggregate output handle registered on the parent, pointing at
        # ``{parent_ws}/out`` — the natural aggregation directory.
        parent_handles = client.portal.call(lambda: app.state.handles.list_by_job(parent_id))
        assert len(parent_handles) == 1
        aggregate = parent_handles[0]
        assert aggregate.output_port_name == "out"
        assert aggregate.path == str(ws_root / "w" / "wf1" / "j" / parent_id / "out")
        assert aggregate.tags == ["frame_sequence"]

        # Every shard's job_assign frame carried shard_element_id +
        # shard_output_prefix.
        frames_sent = [call.args[0] for call in session.ws.send_text.call_args_list]
        assign_frames = [json.loads(f) for f in frames_sent if '"kind":"job_assign"' in f]
        assert len(assign_frames) == 2
        payloads = sorted(
            (env["payload"] for env in assign_frames),
            key=lambda p: p["shard_element_id"],
        )
        assert payloads[0]["shard_element_id"] == "cam_A"
        assert payloads[1]["shard_element_id"] == "cam_B"
        for p in payloads:
            assert p["shard_output_prefix"] == str(ws_root / "w" / "wf1" / "j" / parent_id)


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
            snap = await app.state.workflows.create_snapshot(workflow_id="wf1", graph=graph)
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

        # Gather-all semantics: every shard row is created upfront and
        # every shard runs to a terminal state before the parent is
        # marked FAILED. cam_A completes, cam_B fails, cam_C completes.
        rows = client.portal.call(lambda: app.state.jobs_store.list_recent(100, order="asc"))
        shard_rows = [r for r in rows if r["graph_node_id"] == "fan"]
        # 1 parent + 3 shards (upfront row creation).
        assert len(shard_rows) == 4
        by_state: dict[str, int] = {}
        for r in shard_rows:
            by_state[r["state"]] = by_state.get(r["state"], 0) + 1
        # 1 parent failed, 1 shard failed, 2 shards done.
        assert by_state.get("failed") == 2, by_state
        assert by_state.get("done") == 2, by_state


# ---------------------------------------------------------------------------
# Single-node dispatch fan-out — the ``POST /dispatch/{gnode}`` endpoint
# used to bypass ``_run_fanout_node`` entirely and just call ``_dispatch_job``,
# so running a fan-out node in isolation (via the AlgorithmNode's header
# Run button) blew up because ``{{ inputs.X }}`` rendered to the array
# root instead of a per-shard element. Regression: the endpoint must
# now honor arrayable+arrayed_toggle and spin up a shard fan-out even
# for single-node dispatch.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_node_dispatch_fans_out_arrayable(tmp_path: Path) -> None:
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

        async def _seed() -> str:
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
                workflow_id="wf-dispatch",
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
                workflow_id="wf-dispatch", name="fan-single-dispatch", graph=graph
            )
            snap = await app.state.workflows.create_snapshot(workflow_id="wf-dispatch", graph=graph)
            # Seed the ``src`` slot in the snapshot so the fan-out node's
            # input can be resolved from the snapshot's attributions.
            await app.state.snapshot_jobs.attribute(snap.snapshot_id, upstream.job_id, "src")
            return snap.snapshot_id

        snapshot_id = client.portal.call(_seed)

        # Kick off the shard driver BEFORE dispatch so the shards land in
        # ASSIGNED then get picked up by the background loop as they appear.
        driver_task = client.portal.call(lambda: _drive_shards(app))
        try:
            r = client.post(
                "/api/workflows/wf-dispatch/dispatch/fan",
                params={"base_snapshot_id": snapshot_id},
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["operation"] == "continue"
            # The returned ``job_id`` is the parent coordinator's, not a
            # normal single-shot dispatch — proves fan-out was routed.
            parent_id = body["job_id"]

            # Wait for the background fan-out task to finish (poll the
            # parent job's state; bounded so a bug can't hang the test).
            async def _wait_done() -> Job:
                for _ in range(300):
                    job = await app.state.jobs_store.get(parent_id)
                    if job is not None and job.state in {
                        JobState.DONE,
                        JobState.FAILED,
                    }:
                        return job
                    await asyncio.sleep(0.05)
                raise AssertionError("parent job never reached terminal state")

            parent = client.portal.call(_wait_done)
            assert parent.state is JobState.DONE

            # Shards should exist under this parent.
            shards = client.portal.call(lambda: app.state.jobs_store.list_shards_of(parent_id))
            assert sorted(s.shard_element_id for s in shards) == ["cam_A", "cam_B"]
            for s in shards:
                assert s.state is JobState.DONE

            # Aggregate handle registered on the parent (as with the
            # ``run_snapshot`` path).
            parent_handles = client.portal.call(lambda: app.state.handles.list_by_job(parent_id))
            assert len(parent_handles) == 1
            assert parent_handles[0].output_port_name == "out"

            # Attribution: the fan-out background task attributes the
            # parent to "fan" in the target snapshot. (Each shard is
            # ALSO auto-attributed by ``JobsStore.update`` when it hits
            # DONE — that's a pre-existing quirk of the bridge, not
            # something this test is about; we just assert the parent
            # made it in.)
            async def _wait_attributed() -> list[dict[str, str]]:
                for _ in range(200):
                    rows = await app.state.snapshot_jobs.list_attributions(snapshot_id)
                    fan_rows = [r for r in rows if r["graph_node_id"] == "fan"]
                    if any(r["job_id"] == parent_id for r in fan_rows):
                        return fan_rows
                    await asyncio.sleep(0.05)
                raise AssertionError(
                    f"parent {parent_id} never attributed to 'fan' "
                    f"(rows={rows})"
                )

            fan_rows = client.portal.call(_wait_attributed)
            attributed_ids = {r["job_id"] for r in fan_rows}
            assert parent_id in attributed_ids
        finally:
            client.portal.call(driver_task.cancel)
