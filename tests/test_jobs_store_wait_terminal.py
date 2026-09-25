"""Event-bus semantics for :meth:`JobsStore.wait_terminal`.

Replaces the old 500 ms poll loop in ``execution._await_job_terminal``:
subscribers now fire on the same event-loop tick the DB commit lands.
Tests cover the four cases the fan-out worker actually depends on:

  1. **Immediate return** when the row is already terminal at the moment
     we ask to wait (rerun-from reused rows, snapshot replay, etc.).
  2. **Event-driven wake-up** when the row is still running: a concurrent
     ``store.update`` to DONE / FAILED / CANCELLED / INTERRUPTED unblocks
     us within a tick, not after a poll interval.
  3. **Timeout** semantics: a job that never terminates still fires
     ``asyncio.TimeoutError`` from ``wait_terminal`` (which
     ``execution._await_job_terminal`` maps to ``WorkflowRunError``).
  4. **Multiple waiters** on the same job_id all resolve on one transition
     — a defensive property, not a currently-used code path, but cheap
     to hold true and lets a future retry-observer subscribe alongside.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from hololab.gateway.jobs import Job, JobState, JobStateMachine
from hololab.gateway.registry import JobsStore
from hololab.persistence.db import open_database


async def _running_job(store: JobsStore, job_id: str = "j1") -> Job:
    now = time.time()
    job = Job(
        job_id=job_id,
        workflow_id="w",
        snapshot_id=None,
        algorithm_name="demo",
        algorithm_version="0.1.0",
        state=JobState.RUNNING,
        created_ts=now,
        updated_ts=now,
        started_ts=now,
    )
    await store.create(job)
    return job


@pytest.mark.asyncio
async def test_wait_terminal_returns_immediately_when_already_done(tmp_path: Path) -> None:
    """Race-close: caller decided to wait AFTER the row already flipped
    terminal. The initial ``store.get`` inside ``wait_terminal`` catches
    it and returns without blocking on the future.
    """

    db = await open_database(tmp_path / "s.sqlite")
    try:
        store = JobsStore(db)
        now = time.time()
        done = Job(
            job_id="j-done",
            workflow_id="w",
            snapshot_id=None,
            algorithm_name="demo",
            algorithm_version="0.1.0",
            state=JobState.DONE,
            created_ts=now,
            updated_ts=now,
        )
        await store.create(done)
        result = await asyncio.wait_for(store.wait_terminal("j-done", timeout_s=0.5), timeout=1.0)
        assert result.state is JobState.DONE
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_wait_terminal_fires_on_update_to_done(tmp_path: Path) -> None:
    """The bus wakes the waiter within a tick of the terminal ``update``.

    We schedule the update AFTER the wait starts, then time how long
    ``wait_terminal`` blocks — with the old 500 ms poll this could
    take up to that interval; with the event bus it's a scheduler tick.
    """

    db = await open_database(tmp_path / "s.sqlite")
    try:
        store = JobsStore(db)
        job = await _running_job(store, "j-run")

        async def _flip_to_done_soon() -> None:
            # Small nap so wait_terminal has time to register + do its
            # first store.get() (which sees state=RUNNING).
            await asyncio.sleep(0.02)
            done = JobStateMachine.transition(job, JobState.DONE)
            await store.update(done, "transition:done", "{}")

        t0 = asyncio.get_event_loop().time()
        _flipper = asyncio.create_task(_flip_to_done_soon())
        try:
            result = await asyncio.wait_for(
                store.wait_terminal("j-run", timeout_s=2.0), timeout=3.0
            )
        finally:
            await _flipper
        elapsed = asyncio.get_event_loop().time() - t0

        assert result.state is JobState.DONE
        # Old poll floor was 500 ms. The bus must beat that comfortably —
        # 250 ms is a loose upper bound that catches a regression to
        # polling without being flaky on a busy CI machine.
        assert elapsed < 0.25, f"expected event wake-up, took {elapsed:.3f}s"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_wait_terminal_ignores_non_terminal_updates(tmp_path: Path) -> None:
    """Progress ticks / RUNNING flips don't fulfill the future; the
    waiter blocks until an actual terminal transition. Guards against
    the "wake up on any update" trap, which would return an intermediate
    RUNNING row and mislead the fanout worker into thinking a shard is
    done.
    """

    db = await open_database(tmp_path / "s.sqlite")
    try:
        store = JobsStore(db)
        now = time.time()
        pending = Job(
            job_id="j-pending",
            workflow_id="w",
            snapshot_id=None,
            algorithm_name="demo",
            algorithm_version="0.1.0",
            state=JobState.PENDING,
            created_ts=now,
            updated_ts=now,
        )
        await store.create(pending)

        async def _running_then_stop() -> None:
            await asyncio.sleep(0.02)
            assigned = JobStateMachine.transition(pending, JobState.ASSIGNED, node_id="n1")
            await store.update(assigned, "transition:assigned", "{}")
            running = JobStateMachine.transition(assigned, JobState.RUNNING)
            await store.update(running, "transition:running", "{}")

        _flipper = asyncio.create_task(_running_then_stop())
        try:
            with pytest.raises(asyncio.TimeoutError):
                await store.wait_terminal("j-pending", timeout_s=0.2)
        finally:
            await _flipper
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_wait_terminal_timeout_raises_asyncio_timeout(tmp_path: Path) -> None:
    """When nothing terminates the job, ``wait_terminal`` raises
    ``asyncio.TimeoutError``. Documented contract: ``_await_job_terminal``
    catches this and re-raises as ``WorkflowRunError``. If the
    exception type changes here, the fanout worker's mapping breaks.
    """

    db = await open_database(tmp_path / "s.sqlite")
    try:
        store = JobsStore(db)
        await _running_job(store, "j-stall")
        with pytest.raises(asyncio.TimeoutError):
            await store.wait_terminal("j-stall", timeout_s=0.1)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_wait_terminal_multiple_waiters_on_same_job(tmp_path: Path) -> None:
    """Two subscribers on the same job_id both wake on one terminal
    transition. Defensive — the fan-out worker doesn't currently rely
    on multi-subscriber, but tests that our waiter list actually is a
    list (not a single-slot future that a second subscriber would
    silently replace).
    """

    db = await open_database(tmp_path / "s.sqlite")
    try:
        store = JobsStore(db)
        job = await _running_job(store, "j-multi")

        w1 = asyncio.create_task(store.wait_terminal("j-multi", timeout_s=2.0))
        w2 = asyncio.create_task(store.wait_terminal("j-multi", timeout_s=2.0))
        await asyncio.sleep(0.02)  # let both register

        done = JobStateMachine.transition(job, JobState.DONE)
        await store.update(done, "transition:done", "{}")

        r1, r2 = await asyncio.wait_for(asyncio.gather(w1, w2), timeout=1.0)
        assert r1.state is JobState.DONE
        assert r2.state is JobState.DONE
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_wait_terminal_cleans_up_waiter_on_timeout(tmp_path: Path) -> None:
    """After a timeout the internal waiter map must not leak the future.
    Otherwise a later terminal update on the same job_id would try to
    resolve a cancelled future and might crash the writer callback.
    """

    db = await open_database(tmp_path / "s.sqlite")
    try:
        store = JobsStore(db)
        job = await _running_job(store, "j-cleanup")

        with pytest.raises(asyncio.TimeoutError):
            await store.wait_terminal("j-cleanup", timeout_s=0.05)

        assert "j-cleanup" not in store._terminal_waiters

        # A late terminal update still succeeds — the notify path is a
        # no-op because there are no waiters left in the map, and
        # subsequent waiters on the (already-terminal) row see it via
        # the initial ``store.get``.
        done = JobStateMachine.transition(job, JobState.DONE)
        await store.update(done, "transition:done", "{}")
        result = await store.wait_terminal("j-cleanup", timeout_s=0.5)
        assert result.state is JobState.DONE
    finally:
        await db.close()
