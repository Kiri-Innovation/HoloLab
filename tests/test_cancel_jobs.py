"""Cancel-endpoint regression tests.

Covers the four cancel surfaces the frontend can exercise:

    POST /api/jobs/{job_id}/cancel               — single job, idempotent, cascades to shards
    POST /api/snapshots/{snapshot_id}/cancel-jobs — batch by snapshot
    POST /api/nodes/{node_id}/cancel-jobs         — batch by compute node
    POST /api/jobs/cancel-all                     — global stop

The invariants defended here mirror the design notes in
``gateway/app.py::_cancel_one_job`` + ``_cascade_cancel_shards``:

* An already-terminal job returns 200 (``already_terminal=True``) — no 409.
* A PENDING job with no ``node_id`` still flips to CANCELLED (no WS send).
* Cancelling a fan-out parent cascades to every live shard.
* Batch endpoints skip terminal rows and de-dupe (parent + shard passed
  in together must only signal each row once).
* Sibling graph nodes are untouched by a single-shard cancel.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from hololab.gateway.app import create_app
from hololab.gateway.jobs import Job, JobState
from hololab.gateway.registry import NodeSession
from hololab.protocol.messages import GpuInfo, JobFailReason


def _fake_online_node(client: TestClient, node_id: str = "node-a") -> list[dict[str, Any]]:
    """Register a fake online compute node and record every WS frame the
    gateway would have sent. Mirrors the helper in test_snapshot_delete.
    """

    recorded: list[dict[str, Any]] = []
    ws = MagicMock()

    async def _fake_send(text: str) -> None:
        import json

        recorded.append(json.loads(text))

    ws.send_text = AsyncMock(side_effect=_fake_send)
    session = NodeSession(
        node_id=node_id,
        session_id="sess",
        node_name=f"{node_id}-name",
        ws=ws,
        advertised_url=None,
        gpu=GpuInfo(),
        packs=[],
        protocol_v=1,
    )
    client.app.state.registry._sessions[node_id] = session
    return recorded


async def _mk_job(
    client: TestClient,
    *,
    job_id: str,
    state: JobState,
    node_id: str | None = "node-a",
    workflow_id: str = "wf-1",
    snapshot_id: str | None = None,
    graph_node_id: str | None = None,
    parent_job_id: str | None = None,
    shard_element_id: str | None = None,
) -> Job:
    job = Job(
        job_id=job_id,
        workflow_id=workflow_id,
        snapshot_id=snapshot_id,
        algorithm_name="demo-echo",
        algorithm_version="0.1.0",
        params={},
        input_handles={},
        state=state,
        node_id=node_id,
        graph_node_id=graph_node_id,
        parent_job_id=parent_job_id,
        shard_element_id=shard_element_id,
    )
    await client.app.state.jobs_store.create(job)
    return job


# ---------------------------------------------------------------------------
# Single-job cancel
# ---------------------------------------------------------------------------


def test_cancel_running_job_signals_node_and_transitions(tmp_path: Path) -> None:
    """Live RUNNING job: node receives a ``job_cancel`` frame, DB row
    flips to CANCELLED, WS frame is sent under the session lock."""

    app = create_app(db_path=tmp_path / "cancel.sqlite")
    with TestClient(app) as client:
        recorded = _fake_online_node(client)

        async def _seed() -> None:
            await _mk_job(client, job_id="j1", state=JobState.RUNNING)

        client.portal.call(_seed)

        r = client.post("/api/jobs/j1/cancel")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["state"] == "cancelled"
        assert body["already_terminal"] is False
        assert body["cascaded"] == []

        # Exactly one job_cancel frame reached the node.
        cancel_frames = [f for f in recorded if f["kind"] == "job_cancel"]
        assert len(cancel_frames) == 1
        assert cancel_frames[0]["payload"]["job_id"] == "j1"


def test_cancel_pending_job_without_node_id_still_transitions(tmp_path: Path) -> None:
    """A PENDING job whose ``node_id`` is still NULL (dispatched, pre-assign)
    must still flip to CANCELLED — the DAG loop's ``_await_job_terminal``
    depends on it. No WS frame is possible here.
    """

    app = create_app(db_path=tmp_path / "pending.sqlite")
    with TestClient(app) as client:
        recorded = _fake_online_node(client)

        async def _seed() -> None:
            await _mk_job(client, job_id="j2", state=JobState.PENDING, node_id=None)

        client.portal.call(_seed)

        r = client.post("/api/jobs/j2/cancel")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["state"] == "cancelled"
        assert body["already_terminal"] is False
        assert not any(f["kind"] == "job_cancel" for f in recorded)


def test_cancel_terminal_job_is_idempotent(tmp_path: Path) -> None:
    """DONE / FAILED / CANCELLED rows return 200 with ``already_terminal``
    rather than 409 — no state change, no WS frame."""

    app = create_app(db_path=tmp_path / "terminal.sqlite")
    with TestClient(app) as client:
        recorded = _fake_online_node(client)

        async def _seed() -> None:
            await _mk_job(client, job_id="j-done", state=JobState.DONE)
            await _mk_job(client, job_id="j-fail", state=JobState.FAILED)
            await _mk_job(client, job_id="j-cxl", state=JobState.CANCELLED)

        client.portal.call(_seed)

        for jid, expected in [
            ("j-done", "done"),
            ("j-fail", "failed"),
            ("j-cxl", "cancelled"),
        ]:
            r = client.post(f"/api/jobs/{jid}/cancel")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["state"] == expected
            assert body["already_terminal"] is True

        assert not any(f["kind"] == "job_cancel" for f in recorded)


def test_cancel_unknown_job_returns_404(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "unknown.sqlite")
    with TestClient(app) as client:
        _fake_online_node(client)
        r = client.post("/api/jobs/does-not-exist/cancel")
        assert r.status_code == 404


def test_cancel_running_job_when_node_offline_still_transitions(
    tmp_path: Path,
) -> None:
    """If the assigned node's session dropped, the gateway still
    transitions the job to CANCELLED (the WS frame is silently skipped).
    When the node reconnects it will discover the job is terminal via
    the next heartbeat + state reconciliation.
    """

    app = create_app(db_path=tmp_path / "offline.sqlite")
    with TestClient(app) as client:
        # Deliberately NO fake node session — offline scenario.

        async def _seed() -> None:
            await _mk_job(client, job_id="j-off", state=JobState.RUNNING)

        client.portal.call(_seed)

        r = client.post("/api/jobs/j-off/cancel")
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "cancelled"


# ---------------------------------------------------------------------------
# Fan-out parent cascade
# ---------------------------------------------------------------------------


def test_cancel_fanout_parent_cascades_to_live_shards(tmp_path: Path) -> None:
    """Cancelling a fan-out parent must cancel every live shard row
    beneath it; DONE shards stay DONE (terminal, no change)."""

    app = create_app(db_path=tmp_path / "fanout.sqlite")
    with TestClient(app) as client:
        recorded = _fake_online_node(client)

        async def _seed() -> None:
            await _mk_job(
                client,
                job_id="parent",
                state=JobState.RUNNING,
                graph_node_id="A",
            )
            await _mk_job(
                client,
                job_id="s-done",
                state=JobState.DONE,
                parent_job_id="parent",
                shard_element_id="e0",
                graph_node_id="A",
            )
            await _mk_job(
                client,
                job_id="s-running",
                state=JobState.RUNNING,
                parent_job_id="parent",
                shard_element_id="e1",
                graph_node_id="A",
            )
            await _mk_job(
                client,
                job_id="s-pending",
                state=JobState.PENDING,
                parent_job_id="parent",
                shard_element_id="e2",
                graph_node_id="A",
            )

        client.portal.call(_seed)

        r = client.post("/api/jobs/parent/cancel")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["state"] == "cancelled"
        assert set(body["cascaded"]) == {"s-running", "s-pending"}
        assert "s-done" not in body["cascaded"]

        # 3 job_cancel frames: parent + two live shards. Order isn't
        # asserted — the async fan-out is stable but sequenced by dict
        # iteration.
        cancel_frames = [f for f in recorded if f["kind"] == "job_cancel"]
        assert sorted(f["payload"]["job_id"] for f in cancel_frames) == [
            "parent",
            "s-pending",
            "s-running",
        ]

        # Final DB state per row.
        async def _check() -> None:
            store = client.app.state.jobs_store
            assert (await store.get("parent")).state is JobState.CANCELLED
            assert (await store.get("s-done")).state is JobState.DONE
            assert (await store.get("s-running")).state is JobState.CANCELLED
            assert (await store.get("s-pending")).state is JobState.CANCELLED
            # fail_reason on cancelled rows is set to CANCELLED so
            # downstream analytics can distinguish user-cancel from
            # subprocess-error terminal states.
            cxl = await store.get("s-running")
            assert cxl.fail_reason is JobFailReason.CANCELLED

        client.portal.call(_check)


def test_cancel_shard_does_not_touch_siblings_or_parent(tmp_path: Path) -> None:
    """Cancelling a single shard is a scalpel — sibling shards continue,
    parent's row stays RUNNING (the shard pool coordinator will notice
    the failure and mark it FAILED when the pool drains).
    """

    app = create_app(db_path=tmp_path / "sibling.sqlite")
    with TestClient(app) as client:
        _fake_online_node(client)

        async def _seed() -> None:
            await _mk_job(client, job_id="p", state=JobState.RUNNING)
            await _mk_job(
                client,
                job_id="s0",
                state=JobState.RUNNING,
                parent_job_id="p",
                shard_element_id="e0",
            )
            await _mk_job(
                client,
                job_id="s1",
                state=JobState.RUNNING,
                parent_job_id="p",
                shard_element_id="e1",
            )

        client.portal.call(_seed)

        r = client.post("/api/jobs/s0/cancel")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["state"] == "cancelled"
        assert body["cascaded"] == []

        async def _check() -> None:
            store = client.app.state.jobs_store
            assert (await store.get("p")).state is JobState.RUNNING
            assert (await store.get("s0")).state is JobState.CANCELLED
            assert (await store.get("s1")).state is JobState.RUNNING

        client.portal.call(_check)


# ---------------------------------------------------------------------------
# Batch endpoints
# ---------------------------------------------------------------------------


def test_snapshot_cancel_jobs_hits_only_that_snapshot(tmp_path: Path) -> None:
    """Batch by snapshot: only rows attributed to the target snapshot
    (or with matching ``jobs.snapshot_id``) are touched."""

    app = create_app(db_path=tmp_path / "snap.sqlite")
    with TestClient(app) as client:
        _fake_online_node(client)

        async def _seed() -> None:
            await _mk_job(
                client,
                job_id="in-1",
                state=JobState.RUNNING,
                snapshot_id="snap-x",
                graph_node_id="A",
            )
            await _mk_job(
                client,
                job_id="in-2",
                state=JobState.PENDING,
                snapshot_id="snap-x",
                graph_node_id="B",
            )
            await _mk_job(
                client,
                job_id="other",
                state=JobState.RUNNING,
                snapshot_id="snap-y",
                graph_node_id="A",
            )
            # Terminal but not DONE — DONE rows would trigger a
            # snapshot_jobs FK write against a snapshot we haven't
            # seeded. CANCELLED is a valid terminal state that skips
            # the attribution branch. The point of this seed is to
            # prove the cancel batch does NOT flip a terminal row.
            await _mk_job(
                client,
                job_id="in-cxl",
                state=JobState.CANCELLED,
                snapshot_id="snap-x",
                graph_node_id="C",
            )

        client.portal.call(_seed)

        r = client.post("/api/snapshots/snap-x/cancel-jobs")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["count"] == 2
        assert set(body["cancelled"]) == {"in-1", "in-2"}

        async def _check() -> None:
            store = client.app.state.jobs_store
            assert (await store.get("in-1")).state is JobState.CANCELLED
            assert (await store.get("in-2")).state is JobState.CANCELLED
            assert (await store.get("other")).state is JobState.RUNNING
            assert (await store.get("in-cxl")).state is JobState.CANCELLED

        client.portal.call(_check)


def test_node_cancel_jobs_hits_only_that_node(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "node.sqlite")
    with TestClient(app) as client:
        _fake_online_node(client, node_id="node-a")
        _fake_online_node(client, node_id="node-b")

        async def _seed() -> None:
            await _mk_job(client, job_id="a1", state=JobState.RUNNING, node_id="node-a")
            await _mk_job(client, job_id="a2", state=JobState.ASSIGNED, node_id="node-a")
            await _mk_job(client, job_id="b1", state=JobState.RUNNING, node_id="node-b")
            await _mk_job(client, job_id="a-done", state=JobState.DONE, node_id="node-a")

        client.portal.call(_seed)

        r = client.post("/api/nodes/node-a/cancel-jobs")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["count"] == 2
        assert set(body["cancelled"]) == {"a1", "a2"}

        async def _check() -> None:
            store = client.app.state.jobs_store
            assert (await store.get("a1")).state is JobState.CANCELLED
            assert (await store.get("a2")).state is JobState.CANCELLED
            assert (await store.get("b1")).state is JobState.RUNNING
            assert (await store.get("a-done")).state is JobState.DONE

        client.portal.call(_check)


def test_cancel_all_hits_every_live_job(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "all.sqlite")
    with TestClient(app) as client:
        _fake_online_node(client, node_id="node-a")
        _fake_online_node(client, node_id="node-b")

        async def _seed() -> None:
            await _mk_job(client, job_id="a1", state=JobState.RUNNING, node_id="node-a")
            await _mk_job(client, job_id="b1", state=JobState.ASSIGNED, node_id="node-b")
            await _mk_job(client, job_id="p", state=JobState.PENDING, node_id=None)
            await _mk_job(client, job_id="d", state=JobState.DONE)

        client.portal.call(_seed)

        r = client.post("/api/jobs/cancel-all")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["count"] == 3
        assert set(body["cancelled"]) == {"a1", "b1", "p"}

        # Second call is a no-op: everything's terminal now.
        r2 = client.post("/api/jobs/cancel-all")
        assert r2.status_code == 200
        assert r2.json()["count"] == 0


def test_batch_cancel_dedupes_parent_and_shards(tmp_path: Path) -> None:
    """A fan-out parent + its shards all land in the same snapshot's
    list. The batch endpoint must cancel each row exactly once — no
    duplicates in the returned ``cancelled`` array (a shard hit through
    both direct enumeration and parent cascade)."""

    app = create_app(db_path=tmp_path / "dedupe.sqlite")
    with TestClient(app) as client:
        recorded = _fake_online_node(client)

        async def _seed() -> None:
            await _mk_job(
                client,
                job_id="p",
                state=JobState.RUNNING,
                snapshot_id="snap-1",
                graph_node_id="A",
            )
            await _mk_job(
                client,
                job_id="s0",
                state=JobState.RUNNING,
                snapshot_id="snap-1",
                parent_job_id="p",
                graph_node_id="A",
                shard_element_id="e0",
            )
            await _mk_job(
                client,
                job_id="s1",
                state=JobState.PENDING,
                snapshot_id="snap-1",
                parent_job_id="p",
                graph_node_id="A",
                shard_element_id="e1",
            )

        client.portal.call(_seed)

        r = client.post("/api/snapshots/snap-1/cancel-jobs")
        assert r.status_code == 200, r.text
        body = r.json()
        # Three unique job IDs; no dupes even though parent's cascade
        # and the snapshot's direct enumeration overlap on the shards.
        assert body["count"] == 3
        assert sorted(body["cancelled"]) == ["p", "s0", "s1"]

        # Every row got exactly one job_cancel frame (the ``s1`` shard
        # never dispatched has no node session — but its row does have
        # ``node_id`` set from the seed, so the frame does go out).
        cancel_frames = [f for f in recorded if f["kind"] == "job_cancel"]
        assert sorted(f["payload"]["job_id"] for f in cancel_frames) == [
            "p",
            "s0",
            "s1",
        ]
