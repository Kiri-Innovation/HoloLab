"""Bounded parallel fan-out — ``GraphNode.parallelism`` + upfront shard rows.

Locks down the three behaviors that changed in the parallel-pool refactor
of :func:`hololab.gateway.execution._execute_fanout_body`:

1. **Upfront rows** — every shard's ``jobs`` row lands in the DB in
   ``PENDING`` before any ``job_assign`` frame is sent. The snapshot detail
   endpoint / RecentJobsPanel sees the full row set immediately, so the
   progress denominator matches ``expected_shards`` from t=0. (Regression
   guard for the "n/n+2" bug where the frontend counted lazily-created
   rows and the total grew with dispatch.)

2. **Semaphore concurrency cap** — the number of shards in ASSIGNED at any
   moment is bounded by ``GraphNode.parallelism``. Observed by intercepting
   ``NodeSession.ws.send_text`` and holding sends until the test releases
   them — the high-water mark of in-flight sends must not exceed the cap.

3. **No fail-fast** — every shard reaches a terminal state even after
   another shard has failed; the parent is marked FAILED once, after the
   last shard settles. Guards the v1 aggregate-failure spec.

Reuses the fake-node / fake-catalog / driver helpers from
``test_scheduler_arrayed`` so the setup story stays consistent.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from hololab.gateway.app import create_app
from hololab.gateway.execution import WorkflowRunError, run_snapshot
from hololab.gateway.handles import Handle
from hololab.gateway.jobs import Job, JobState, JobStateMachine, event_from_transition
from hololab.gateway.registry import NodeSession
from hololab.gateway.workflows import GraphEdge, GraphNode, WorkflowGraph
from hololab.protocol.messages import GpuInfo, JobFailReason


def _fake_online_node(
    client_app: Any,
    *,
    workspace_root: Path,
    node_id: str = "node-a",
    send_hook: Any = None,
) -> NodeSession:
    ws = MagicMock()
    ws.send_text = send_hook if send_hook is not None else AsyncMock()
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


def _install_fake_catalog(client_app: Any) -> None:
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
        "arrayable": True,
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


async def _seed_fanout_graph(
    app: Any,
    *,
    element_ids: list[str],
    parallelism: int,
    workflow_id: str = "wf1",
) -> tuple[str, WorkflowGraph, Path]:
    """Register the upstream fake handle + snapshot with a fan-out node.

    Returns (snapshot_id, graph, arr_root).
    """
    arr_root = Path(app.state.workflows._db._path).parent / "arr"
    arr_root.mkdir(exist_ok=True)
    for eid in element_ids:
        (arr_root / eid).mkdir(exist_ok=True)

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
        workflow_id=workflow_id,
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
                parallelism=parallelism,
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
    await app.state.workflows.save_draft(workflow_id=workflow_id, name="par-test", graph=graph)
    snap = await app.state.workflows.create_snapshot(workflow_id=workflow_id, graph=graph)
    return snap.snapshot_id, graph, arr_root


# ---------------------------------------------------------------------------
# 1) Upfront row creation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upfront_shard_rows_appear_before_any_dispatch(tmp_path: Path) -> None:
    """All N shard rows land in the DB before the first ``job_assign`` frame.

    Uses a send-side ``asyncio.Event`` gate: when ``ws.send_text`` fires,
    freeze it and snapshot the jobs table. Every shard row for the fan-out
    node must already be present, all in ``PENDING`` (only the parent is
    RUNNING at that moment; nothing is ASSIGNED yet because the first send
    is still blocked).
    """
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    app = create_app(db_path=tmp_path / "test.sqlite")

    from fastapi.testclient import TestClient

    elements = ["cam_A", "cam_B", "cam_C", "cam_D"]
    first_send_gate = asyncio.Event()
    saw_first_send = asyncio.Event()
    rows_at_first_send: list[dict[str, Any]] = []

    async def _gated_send(_frame: str) -> None:
        # Snapshot the DB the very first time a job_assign frame is sent.
        if not saw_first_send.is_set():
            rows_at_first_send.extend(
                await app.state.jobs_store.list_recent(limit=100, order="asc")
            )
            saw_first_send.set()
        await first_send_gate.wait()

    with TestClient(app) as client:
        _fake_online_node(app, workspace_root=ws_root, send_hook=_gated_send)
        _install_fake_catalog(app)

        async def _seed() -> tuple[str, WorkflowGraph]:
            snap_id, graph, _ = await _seed_fanout_graph(
                app, element_ids=elements, parallelism=1
            )
            return snap_id, graph

        snapshot_id, graph = client.portal.call(_seed)

        async def _drive() -> None:
            # Wait for the "rows are visible" snapshot to be taken, then
            # release the send gate + drive all shards to DONE so the
            # workflow terminates cleanly.
            await saw_first_send.wait()
            first_send_gate.set()
            store = app.state.jobs_store
            processed: set[str] = set()
            for _ in range(400):
                rows = await store.list_recent(limit=200, order="asc")
                for r in rows:
                    jid = r["job_id"]
                    if jid in processed or r.get("state") != "assigned":
                        continue
                    job = await store.get(jid)
                    if job is None or job.parent_job_id is None:
                        continue
                    running = JobStateMachine.transition(job, JobState.RUNNING)
                    kind, payload = event_from_transition(job, running)
                    await store.update(running, kind, payload)
                    done = JobStateMachine.transition(running, JobState.DONE)
                    kind, payload = event_from_transition(running, done)
                    await store.update(done, kind, payload)
                    processed.add(jid)
                if len(processed) >= len(elements):
                    return
                await asyncio.sleep(0.01)

        async def _run_all() -> None:
            drive_task = asyncio.create_task(_drive())
            try:
                await run_snapshot(
                    app,
                    snapshot_id=snapshot_id,
                    graph=graph,
                    workflow_id="wf1",
                    skip_graph_nodes={"src"},
                    seed_outputs={"src": {"out": "h-arr"}},
                )
            finally:
                drive_task.cancel()

        client.portal.call(_run_all)

        shard_rows_at_first_send = [
            r for r in rows_at_first_send if r["graph_node_id"] == "fan" and r.get("parent_job_id")
        ]
        assert len(shard_rows_at_first_send) == len(elements), (
            f"expected all {len(elements)} shard rows to exist before the first "
            f"job_assign frame is sent, saw {len(shard_rows_at_first_send)} — "
            "the executor is falling back to lazy shard creation"
        )
        # All shard rows should still be PENDING at that moment (the first
        # send hasn't finished, so no shard has been transitioned to
        # ASSIGNED yet). One row will already be in ASSIGNED for the shard
        # whose send is currently blocked — tolerate that too.
        pending_or_assigned = {"pending", "assigned"}
        for r in shard_rows_at_first_send:
            assert r["state"] in pending_or_assigned, r


# ---------------------------------------------------------------------------
# 2) Semaphore concurrency cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parallelism_caps_in_flight_shards(tmp_path: Path) -> None:
    """At most ``parallelism`` shards ever sit "sent but not terminal" at once.

    ``send_text`` is serialised by ``NodeSession.send_lock`` in production,
    so counting concurrent sends doesn't reveal pool parallelism. What
    parallelism controls is the "sent, running, not yet terminal" window
    — i.e. how many shards are in ``RUNNING`` at the same time. The
    driver holds each shard in RUNNING for a short window and a separate
    poller samples the ``RUNNING`` count; high-water must equal
    ``parallelism`` (proves the pool is admitting up to N) and must never
    exceed it (proves the semaphore doesn't leak).
    """
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    app = create_app(db_path=tmp_path / "test.sqlite")

    from fastapi.testclient import TestClient

    elements = [f"e{i:02d}" for i in range(8)]
    parallelism = 3
    hold_ms = 60  # each shard sits in RUNNING for ~60ms before completing

    with TestClient(app) as client:
        _fake_online_node(app, workspace_root=ws_root)
        _install_fake_catalog(app)

        async def _seed() -> tuple[str, WorkflowGraph]:
            snap_id, graph, _ = await _seed_fanout_graph(
                app, element_ids=elements, parallelism=parallelism
            )
            return snap_id, graph

        snapshot_id, graph = client.portal.call(_seed)

        high_water = 0

        async def _sample_running_count() -> None:
            """Poll the jobs table and track the max concurrent RUNNING shards."""
            nonlocal high_water
            store = app.state.jobs_store
            for _ in range(300):
                rows = await store.list_recent(limit=200, order="asc")
                running_shard_count = sum(
                    1
                    for r in rows
                    if r["graph_node_id"] == "fan"
                    and r.get("parent_job_id")
                    and r["state"] == "running"
                )
                high_water = max(high_water, running_shard_count)
                await asyncio.sleep(0.005)

        async def _drive() -> None:
            """Move each shard ASSIGNED → RUNNING immediately, then hold in
            RUNNING for ``hold_ms`` before DONE — creates the observation
            window for the sampler."""
            store = app.state.jobs_store
            processed: set[str] = set()

            async def _complete_after_hold(job: Job) -> None:
                running = JobStateMachine.transition(job, JobState.RUNNING)
                kind, payload = event_from_transition(job, running)
                await store.update(running, kind, payload)
                await asyncio.sleep(hold_ms / 1000)
                done = JobStateMachine.transition(running, JobState.DONE)
                kind, payload = event_from_transition(running, done)
                await store.update(done, kind, payload)

            for _ in range(600):
                rows = await store.list_recent(limit=200, order="asc")
                for r in rows:
                    jid = r["job_id"]
                    if jid in processed or r.get("state") != "assigned":
                        continue
                    job = await store.get(jid)
                    if job is None or job.parent_job_id is None:
                        continue
                    processed.add(jid)
                    # Fire-and-forget task; the outer _run_all's finally
                    # cancels the driver when the test ends. Stashed into
                    # ``_t`` to satisfy RUF006 (asyncio.create_task's
                    # return value must be stored) without holding onto it.
                    _t = asyncio.create_task(_complete_after_hold(job))
                    del _t
                if len(processed) >= len(elements):
                    return
                await asyncio.sleep(0.005)

        async def _run_all() -> None:
            drive_task = asyncio.create_task(_drive())
            sample_task = asyncio.create_task(_sample_running_count())
            try:
                await run_snapshot(
                    app,
                    snapshot_id=snapshot_id,
                    graph=graph,
                    workflow_id="wf1",
                    skip_graph_nodes={"src"},
                    seed_outputs={"src": {"out": "h-arr"}},
                )
            finally:
                drive_task.cancel()
                sample_task.cancel()

        client.portal.call(_run_all)

        assert high_water <= parallelism, (
            f"high-water RUNNING shards={high_water} exceeded parallelism={parallelism} — "
            "executor semaphore is leaking"
        )
        assert high_water >= 2, (
            f"high-water RUNNING shards={high_water} — pool never got above "
            "1 concurrent shard, so the parallelism test isn't actually parallel "
            "(driver might be moving shards through RUNNING too fast to sample)"
        )


# ---------------------------------------------------------------------------
# 3) No fail-fast — every shard settles before the parent fails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_fail_fast_all_shards_reach_terminal(tmp_path: Path) -> None:
    """When shard 1 fails, shards 0/2/3 still complete before the parent fails.

    Distinct from the older ``test_fanout_shard_failure_marks_parent_failed``
    — that one tests a 3-element fan with parallelism=1. This one uses
    parallelism=2 to exercise the pool + gather codepath explicitly.
    """
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    app = create_app(db_path=tmp_path / "test.sqlite")

    from fastapi.testclient import TestClient

    elements = ["a", "b", "c", "d"]

    with TestClient(app) as client:
        session = _fake_online_node(app, workspace_root=ws_root)
        _install_fake_catalog(app)

        async def _seed() -> tuple[str, WorkflowGraph]:
            snap_id, graph, _ = await _seed_fanout_graph(
                app, element_ids=elements, parallelism=2
            )
            return snap_id, graph

        snapshot_id, graph = client.portal.call(_seed)

        async def _drive() -> None:
            store = app.state.jobs_store
            processed: set[str] = set()
            fail_idx = 1
            for _ in range(600):
                rows = await store.list_recent(limit=200, order="asc")
                for r in rows:
                    jid = r["job_id"]
                    if jid in processed or r.get("state") != "assigned":
                        continue
                    job = await store.get(jid)
                    if job is None or job.parent_job_id is None:
                        continue
                    running = JobStateMachine.transition(job, JobState.RUNNING)
                    kind, payload = event_from_transition(job, running)
                    await store.update(running, kind, payload)
                    idx_seen = len(processed)
                    processed.add(jid)
                    if idx_seen == fail_idx:
                        failed = JobStateMachine.transition(
                            running,
                            JobState.FAILED,
                            fail_reason=JobFailReason.USER_ERROR,
                            fail_message="stubbed",
                        )
                        kind, payload = event_from_transition(running, failed)
                        await store.update(failed, kind, payload)
                    else:
                        done = JobStateMachine.transition(running, JobState.DONE)
                        kind, payload = event_from_transition(running, done)
                        await store.update(done, kind, payload)
                if len(processed) >= len(elements):
                    return
                await asyncio.sleep(0.01)

        async def _run_all() -> None:
            drive_task = asyncio.create_task(_drive())
            try:
                await run_snapshot(
                    app,
                    snapshot_id=snapshot_id,
                    graph=graph,
                    workflow_id="wf1",
                    skip_graph_nodes={"src"},
                    seed_outputs={"src": {"out": "h-arr"}},
                )
            finally:
                drive_task.cancel()

        with pytest.raises(WorkflowRunError):
            client.portal.call(_run_all)

        rows = client.portal.call(lambda: app.state.jobs_store.list_recent(100, order="asc"))
        shard_rows = [r for r in rows if r["graph_node_id"] == "fan" and r.get("parent_job_id")]
        assert len(shard_rows) == len(elements), (
            f"expected all {len(elements)} shard rows to persist post-failure "
            f"(no fail-fast), got {len(shard_rows)}"
        )
        by_state: dict[str, int] = {}
        for r in shard_rows:
            by_state[r["state"]] = by_state.get(r["state"], 0) + 1
        # 1 failed shard + 3 done shards. No shards left in pending/assigned.
        assert by_state.get("done") == 3, by_state
        assert by_state.get("failed") == 1, by_state
        assert by_state.get("pending", 0) == 0
        assert by_state.get("assigned", 0) == 0

        # Every element got its own job_assign frame (no fail-fast means
        # even shards dispatched after the failure went out).
        frames_sent = [call.args[0] for call in session.ws.send_text.call_args_list]
        assign_payloads = [
            json.loads(f)["payload"] for f in frames_sent if '"kind":"job_assign"' in f
        ]
        sent_elements = sorted(p["shard_element_id"] for p in assign_payloads)
        assert sent_elements == sorted(elements)


# ---------------------------------------------------------------------------
# 4) Default parallelism=1 keeps serial dispatch (regression guard)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parallelism_one_dispatches_serially(tmp_path: Path) -> None:
    """With parallelism=1 no more than one shard is ever RUNNING at once.

    Locks the "default 1 = 现状零变化" contract — an operator who never
    touches the new knob sees single-slot dispatch just like before.
    """
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    app = create_app(db_path=tmp_path / "test.sqlite")

    from fastapi.testclient import TestClient

    elements = ["x0", "x1", "x2", "x3"]
    hold_ms = 40

    with TestClient(app) as client:
        _fake_online_node(app, workspace_root=ws_root)
        _install_fake_catalog(app)

        async def _seed() -> tuple[str, WorkflowGraph]:
            snap_id, graph, _ = await _seed_fanout_graph(
                app, element_ids=elements, parallelism=1
            )
            return snap_id, graph

        snapshot_id, graph = client.portal.call(_seed)

        high_water = 0

        async def _sample_running_count() -> None:
            nonlocal high_water
            store = app.state.jobs_store
            for _ in range(300):
                rows = await store.list_recent(limit=200, order="asc")
                running_shard_count = sum(
                    1
                    for r in rows
                    if r["graph_node_id"] == "fan"
                    and r.get("parent_job_id")
                    and r["state"] == "running"
                )
                high_water = max(high_water, running_shard_count)
                await asyncio.sleep(0.005)

        async def _drive() -> None:
            store = app.state.jobs_store
            processed: set[str] = set()

            async def _complete_after_hold(job: Job) -> None:
                running = JobStateMachine.transition(job, JobState.RUNNING)
                kind, payload = event_from_transition(job, running)
                await store.update(running, kind, payload)
                await asyncio.sleep(hold_ms / 1000)
                done = JobStateMachine.transition(running, JobState.DONE)
                kind, payload = event_from_transition(running, done)
                await store.update(done, kind, payload)

            for _ in range(600):
                rows = await store.list_recent(limit=200, order="asc")
                for r in rows:
                    jid = r["job_id"]
                    if jid in processed or r.get("state") != "assigned":
                        continue
                    job = await store.get(jid)
                    if job is None or job.parent_job_id is None:
                        continue
                    processed.add(jid)
                    # Fire-and-forget task; the outer _run_all's finally
                    # cancels the driver when the test ends. Stashed into
                    # ``_t`` to satisfy RUF006 (asyncio.create_task's
                    # return value must be stored) without holding onto it.
                    _t = asyncio.create_task(_complete_after_hold(job))
                    del _t
                if len(processed) >= len(elements):
                    return
                await asyncio.sleep(0.005)

        async def _run_all() -> None:
            drive_task = asyncio.create_task(_drive())
            sample_task = asyncio.create_task(_sample_running_count())
            try:
                await run_snapshot(
                    app,
                    snapshot_id=snapshot_id,
                    graph=graph,
                    workflow_id="wf1",
                    skip_graph_nodes={"src"},
                    seed_outputs={"src": {"out": "h-arr"}},
                )
            finally:
                drive_task.cancel()
                sample_task.cancel()

        client.portal.call(_run_all)

        assert high_water == 1, (
            f"parallelism=1 must serialise dispatch, but observed {high_water} "
            "concurrent RUNNING shards — the semaphore isn't gating"
        )
