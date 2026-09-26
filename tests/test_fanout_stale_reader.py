"""Reader-snapshot lag between phase-1 commit and phase-2 read must not
strand shards in PENDING.

Regression for the 2026-09-26 incident: a 100-shard colmap-triangulate
fan-out (``par=5``) landed with 95 DONE + 5 PENDING and a parent marked
``failed / user_error`` with fail_message ``"shard N (element frame_000N)
finished pending"`` for N in 0..4. All 5 stranded rows were the ones
whose ``_run_one`` grabbed the semaphore first — microseconds after
``store.create_many`` committed on the writer connection. Their
``store.get(shard.job_id)`` returned ``None`` (the reader's WAL
snapshot had not yet advanced), so ``_run_one`` short-circuited via
``if shard_now is None: return (idx, element_id, shard)``. The
in-memory ``shard`` was still PENDING; the aggregator saw a
non-DONE terminal and marked the parent failed.

The fix: on ``None`` the reader is lying about our own just-committed
write, not reporting a genuinely deleted row (nothing else in the
gateway DELETEs from ``jobs`` mid-fan-out — grep for ``DELETE FROM
jobs`` — only ``snapshot_delete`` does, and it can't fire while the
parent is alive). Trust the in-memory shard and dispatch anyway.

This test simulates the lag by monkey-patching ``JobsStore.get`` so
each shard's very first read returns ``None`` once; subsequent reads
return the real row. Without the fix the parent aggregator marks
FAILED with "finished pending" for every shard. With the fix all
shards dispatch cleanly and the parent reaches DONE.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hololab.gateway.app import create_app
from hololab.gateway.execution import run_snapshot
from hololab.gateway.jobs import Job, JobState, JobStateMachine, event_from_transition
from hololab.gateway.workflows import WorkflowGraph
from tests.test_fanout_parallelism import (
    _fake_online_node,
    _install_fake_catalog,
    _seed_fanout_graph,
)


@pytest.mark.asyncio
async def test_stale_reader_none_does_not_strand_shard(tmp_path: Path) -> None:
    """First ``store.get`` per shard returns ``None`` — dispatch must still fire.

    The workflow completes successfully: parent DONE, every shard DONE,
    zero "finished pending" fingerprints in the parent's fail_message.
    Without the fix the first ``parallelism`` shards would strand in
    PENDING and the parent would carry the incident's exact aggregator
    message.
    """

    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    app = create_app(db_path=tmp_path / "stale.sqlite")

    elements = ["a", "b", "c", "d"]
    parallelism = 2

    with TestClient(app) as client:
        _fake_online_node(app, workspace_root=ws_root)
        _install_fake_catalog(app)

        async def _seed() -> tuple[str, WorkflowGraph]:
            snap_id, graph, _ = await _seed_fanout_graph(
                app, element_ids=elements, parallelism=parallelism
            )
            return snap_id, graph

        snapshot_id, graph = client.portal.call(_seed)

        # Wrap JobsStore.get so that each shard-shaped job_id (i.e. any
        # id created AFTER we install the wrapper — the parent + shard
        # rows for this fan-out) returns None on its very first read.
        # The parent read that happens BEFORE the wrapper is installed
        # is unaffected; subsequent reads (state re-check, dispatch,
        # driver, terminal wait) all see the real row.
        store = app.state.jobs_store
        real_get = store.get
        first_seen: set[str] = set()

        async def _stale_first_get(job_id: str) -> Job | None:
            if job_id not in first_seen:
                first_seen.add(job_id)
                # One-shot lie: pretend the reader hasn't caught up yet.
                return None
            return await real_get(job_id)

        store.get = _stale_first_get  # type: ignore[method-assign]

        async def _drive() -> None:
            processed: set[str] = set()
            for _ in range(600):
                rows = await real_get.__self__.list_recent(  # type: ignore[attr-defined]
                    limit=200, order="asc"
                )
                for r in rows:
                    jid = r["job_id"]
                    if jid in processed or r.get("state") != "assigned":
                        continue
                    job = await real_get(jid)
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
                    job_timeout_s=30,
                )
            finally:
                drive_task.cancel()

        client.portal.call(_run_all)

        async def _check() -> None:
            rows = await real_get.__self__.list_recent(  # type: ignore[attr-defined]
                limit=200, order="asc"
            )
            fan_rows = [r for r in rows if r["graph_node_id"] == "fan"]
            shard_rows = [r for r in fan_rows if r.get("parent_job_id")]
            parent_rows = [r for r in fan_rows if not r.get("parent_job_id")]

            assert len(shard_rows) == len(elements), [r["state"] for r in shard_rows]
            for r in shard_rows:
                assert r["state"] == "done", (
                    f"shard {r['job_id']} left in {r['state']!r} — stale-reader "
                    "fast-return stranded it in PENDING (regression)"
                )

            assert len(parent_rows) == 1
            parent = await real_get(parent_rows[0]["job_id"])
            assert parent is not None
            assert parent.state is JobState.DONE, (
                f"parent left in {parent.state.value!r} with fail_message="
                f"{parent.fail_message!r} — stale-reader race re-introduced"
            )
            # Belt-and-braces on the exact 2026-09-26 fingerprint.
            assert parent.fail_message is None or "finished pending" not in (parent.fail_message), (
                parent.fail_message
            )

            # Every shard must actually have been lied to on its first
            # read — otherwise the test isn't exercising the race path.
            shard_ids = {r["job_id"] for r in shard_rows}
            assert shard_ids.issubset(first_seen), (
                "at least one shard was never subjected to the stale-reader "
                "wrapper; test setup is not exercising the race"
            )

        client.portal.call(_check)
