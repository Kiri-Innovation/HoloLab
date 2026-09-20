"""Gateway startup → orphan → reconcile flow.

The regression this file is protecting against: a dev-time gateway
bounce used to mark every in-flight row ``interrupted`` (terminal) and
lose real, still-running work by the shovelful. The new flow:

    startup: assigned/running → orphaned (recoverable)
    register: node's live running_jobs → orphaned rows hoisted back
              to running; jobs the node no longer claims → interrupted
    sweeper:  after ORPHAN_GRACE_S, any orphan whose owner never came
              back is finalised to interrupted

These tests hit the DB-level helpers directly. End-to-end coverage
(a fan-out surviving a real gateway bounce) is out of scope for a
unit test; the runtime resilience tests exercise the node side.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from hololab.gateway.jobs import Job, JobState, JobStateMachine
from hololab.gateway.registry import (
    JobsStore,
    finalize_stale_orphaned_jobs,
    mark_stuck_jobs_orphaned,
)
from hololab.persistence.db import open_database


async def _seed_job(
    db,
    job_id: str,
    state: str,
    *,
    node_id: str | None = None,
    updated_ts: float | None = None,
) -> None:
    """Insert one minimal job row with a caller-chosen state + owner."""

    now = time.time()

    async def _write(conn) -> None:
        await conn.execute(
            "INSERT INTO jobs (job_id, workflow_id, algorithm_name, algorithm_version, "
            "params_json, input_handles_json, state, node_id, created_ts, updated_ts) "
            "VALUES (?, 'w', 'demo-echo', '0.1.0', '{}', '{}', ?, ?, ?, ?)",
            (job_id, state, node_id, now, updated_ts if updated_ts is not None else now),
        )

    await db.write(_write)


@pytest.mark.asyncio
async def test_mark_stuck_orphaned_skips_pending_and_orphaned_and_terminal(
    tmp_path: Path,
) -> None:
    """Only ``assigned`` and ``running`` are reclaimed; everything else
    is either already-recoverable (pending, orphaned) or terminal
    (done/failed/cancelled/interrupted).
    """

    db = await open_database(tmp_path / "s.sqlite")
    try:
        for jid, state in [
            ("pending", "pending"),
            ("assigned", "assigned"),
            ("running", "running"),
            ("orphaned", "orphaned"),
            ("done", "done"),
            ("failed", "failed"),
            ("cancelled", "cancelled"),
            ("interrupted", "interrupted"),
        ]:
            await _seed_job(db, jid, state)

        n = await mark_stuck_jobs_orphaned(db)
        assert n == 2

        store = JobsStore(db)
        assert (await store.get("pending")).state.value == "pending"
        assert (await store.get("assigned")).state.value == "orphaned"
        assert (await store.get("running")).state.value == "orphaned"
        assert (await store.get("orphaned")).state.value == "orphaned"
        # Terminals untouched.
        for jid, expected in [
            ("done", "done"),
            ("failed", "failed"),
            ("cancelled", "cancelled"),
            ("interrupted", "interrupted"),
        ]:
            assert (await store.get(jid)).state.value == expected
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_finalize_stale_orphaned_leaves_recent_alone(tmp_path: Path) -> None:
    """Orphans younger than the cutoff MUST stay — that's the grace
    window that gives slow-to-reconnect nodes a chance to claim their
    jobs at register time.
    """

    db = await open_database(tmp_path / "s.sqlite")
    try:
        old_ts = time.time() - 600.0
        recent_ts = time.time() - 30.0
        await _seed_job(db, "old", "orphaned", node_id="n1", updated_ts=old_ts)
        await _seed_job(db, "recent", "orphaned", node_id="n1", updated_ts=recent_ts)

        # Cutoff 300s ago: "old" (600s ago) is past it, "recent" (30s ago) isn't.
        finalized = await finalize_stale_orphaned_jobs(db, cutoff_ts=time.time() - 300.0)
        assert finalized == ["old"]

        store = JobsStore(db)
        assert (await store.get("old")).state.value == "interrupted"
        assert (await store.get("recent")).state.value == "orphaned"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_finalize_stale_orphaned_excludes_connected_nodes(tmp_path: Path) -> None:
    """A node that's currently connected is doing its own reconcile at
    register time — the sweeper must not race that path. Excluding
    connected node_ids keeps the sweep well-behaved even when a slow
    reconcile straddles the cutoff.
    """

    db = await open_database(tmp_path / "s.sqlite")
    try:
        old_ts = time.time() - 600.0
        await _seed_job(db, "connected-node-job", "orphaned", node_id="n1", updated_ts=old_ts)
        await _seed_job(db, "gone-node-job", "orphaned", node_id="n2", updated_ts=old_ts)

        finalized = await finalize_stale_orphaned_jobs(
            db,
            cutoff_ts=time.time() - 300.0,
            exclude_node_ids={"n1"},
        )
        assert finalized == ["gone-node-job"]

        store = JobsStore(db)
        assert (await store.get("connected-node-job")).state.value == "orphaned"
        assert (await store.get("gone-node-job")).state.value == "interrupted"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_list_orphaned_for_node_returns_only_this_owner(tmp_path: Path) -> None:
    """The register-time reconciler queries orphans by owner. Rows
    owned by other nodes must not leak into the reconcile set — that
    would let node A's register accidentally hoist node B's job.
    """

    db = await open_database(tmp_path / "s.sqlite")
    try:
        await _seed_job(db, "a1", "orphaned", node_id="node-A")
        await _seed_job(db, "a2", "orphaned", node_id="node-A")
        await _seed_job(db, "b1", "orphaned", node_id="node-B")
        await _seed_job(db, "unowned", "orphaned", node_id=None)

        store = JobsStore(db)
        got = await store.list_orphaned_for_node("node-A")
        assert sorted(j.job_id for j in got) == ["a1", "a2"]
    finally:
        await db.close()


def test_state_machine_allows_orphaned_to_done() -> None:
    """Terminal-state race: a ``job_done`` frame that lands before the
    register-time hoist to RUNNING has to be accepted, otherwise the
    state machine would drop the completion.
    """

    job = Job(
        job_id="j",
        workflow_id="w",
        snapshot_id="s",
        algorithm_name="demo-echo",
        algorithm_version="0.1.0",
        state=JobState.ORPHANED,
    )
    new = JobStateMachine.transition(job, JobState.DONE)
    assert new.state is JobState.DONE


def test_state_machine_still_forbids_interrupted_outbound() -> None:
    """INTERRUPTED remains terminal — the fix widens the recovery window
    (via ORPHANED) rather than un-terminaling the previous terminal.
    """

    from hololab.gateway.jobs import IllegalTransition

    job = Job(
        job_id="j",
        workflow_id="w",
        snapshot_id="s",
        algorithm_name="demo-echo",
        algorithm_version="0.1.0",
        state=JobState.INTERRUPTED,
    )
    for target in (
        JobState.RUNNING,
        JobState.DONE,
        JobState.FAILED,
        JobState.ORPHANED,
        JobState.CANCELLED,
    ):
        with pytest.raises(IllegalTransition):
            JobStateMachine.transition(job, target)


@pytest.mark.asyncio
async def test_reconcile_hoists_claimed_and_finalises_unclaimed(tmp_path: Path) -> None:
    """End-to-end DB path for one node's reconcile.

    Mirrors what ``_reconcile_orphaned_on_register`` does with a live
    node session (side-effect-free part only — no hub broadcasts):
      * orphans in the node's running_jobs → RUNNING
      * orphans NOT in running_jobs → INTERRUPTED
    """

    from hololab.gateway.jobs import event_from_transition

    db = await open_database(tmp_path / "s.sqlite")
    try:
        await _seed_job(db, "still-alive", "orphaned", node_id="n1")
        await _seed_job(db, "silently-done", "orphaned", node_id="n1")
        await _seed_job(db, "someone-elses", "orphaned", node_id="n2")

        store = JobsStore(db)
        orphans = await store.list_orphaned_for_node("n1")
        claimed = {"still-alive"}

        for job in orphans:
            target = JobState.RUNNING if job.job_id in claimed else JobState.INTERRUPTED
            new = JobStateMachine.transition(job, target)
            kind, payload = event_from_transition(job, new)
            await store.update(new, kind, payload)

        assert (await store.get("still-alive")).state.value == "running"
        assert (await store.get("silently-done")).state.value == "interrupted"
        # Untouched — we filtered by node_id.
        assert (await store.get("someone-elses")).state.value == "orphaned"
    finally:
        await db.close()
