"""``SnapshotJobsStore.get_job_at`` prefers parent over shard for fan-out slots.

The bug this defends against
----------------------------

An arrayed<T> fan-out writes one parent + N shard rows into
``snapshot_jobs`` — all with the same ``(snapshot_id, graph_node_id)``.
The parent produces the fanned-in arrayed<T> output; each shard produces
only its per-element slice. Downstream input resolution reads
``get_job_at(snap, gnid)`` and passes the result to
``HandleBook.list_by_job(job_id)`` to find the source handle for an edge
— it MUST get the parent's job_id, not a shard's, otherwise the wiring
resolves to a single shard's output and the downstream node sees one
slice instead of the aggregated arrayed<T>.

Before this fix the query was ``LIMIT 1`` with no ORDER BY, so SQLite
returned an arbitrary row. Whichever row hit first — parent or shard —
was pure implementation detail (row-id assignment, INSERT order under
concurrent commits, etc.). The rank now prefers rows whose
``jobs.parent_job_id IS NULL`` (parents), with newest ``created_ts``
breaking ties.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hololab.gateway.jobs import Job, JobState
from hololab.gateway.registry import JobsStore, SnapshotJobsStore
from hololab.gateway.workflows import GraphNode, WorkflowGraph, WorkflowStore
from hololab.persistence.db import open_database


def _one_node_graph(gnid: str) -> WorkflowGraph:
    return WorkflowGraph(
        nodes=[
            GraphNode(
                id=gnid,
                algorithm_name="demo",
                algorithm_version="0.1.0",
                assigned_node_id=None,
            ),
        ],
        edges=[],
    )


async def _fresh_stores(
    tmp_path: Path,
) -> tuple[JobsStore, SnapshotJobsStore, WorkflowStore, object]:
    db = await open_database(tmp_path / "hl.db")
    return JobsStore(db), SnapshotJobsStore(db), WorkflowStore(db), db


@pytest.mark.asyncio
async def test_get_job_at_prefers_parent_over_shards(tmp_path: Path) -> None:
    """Attribute a parent + several shards to the same slot; expect parent."""

    jobs_store, snapshot_jobs, workflows, db = await _fresh_stores(tmp_path)
    try:
        gnid = "fan"
        graph = _one_node_graph(gnid)
        wf = await workflows.save_draft(workflow_id=None, name="fan-test", graph=graph)
        snap = await workflows.create_snapshot(workflow_id=wf.workflow_id, graph=graph)

        # Parent created FIRST (matches production order — the parent job
        # row lands before the fan-out loop mints shard rows).
        parent = Job(
            job_id="job-parent",
            snapshot_id=snap.snapshot_id,
            workflow_id=wf.workflow_id,
            graph_node_id=gnid,
            algorithm_name="demo",
            algorithm_version="0.1.0",
            state=JobState.DONE,
        )
        await jobs_store.create(parent)  # also writes snapshot_jobs (DONE + snap + gnid)

        for i, elem in enumerate(["cam_A", "cam_B", "cam_C"]):
            shard = Job(
                job_id=f"job-shard-{elem}",
                snapshot_id=snap.snapshot_id,
                workflow_id=wf.workflow_id,
                graph_node_id=gnid,
                algorithm_name="demo",
                algorithm_version="0.1.0",
                state=JobState.DONE,
                parent_job_id=parent.job_id,
                shard_element_id=elem,
                # Slightly newer created_ts so the parent doesn't accidentally
                # win by ORDER BY created_ts alone — the parent-first rank
                # has to be the load-bearing clause here.
                created_ts=parent.created_ts + 0.001 * (i + 1),
            )
            await jobs_store.create(shard)

        picked = await snapshot_jobs.get_job_at(snap.snapshot_id, gnid)
        assert picked == parent.job_id, (
            f"expected parent {parent.job_id!r}, got {picked!r} — "
            "get_job_at must rank parent (parent_job_id IS NULL) ahead of "
            "shards so downstream input resolution binds to the arrayed<T> "
            "fan-in, not a single shard's slice."
        )
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_get_job_at_returns_none_when_slot_empty(tmp_path: Path) -> None:
    """Negative case — an empty slot still returns None (Continue vs Fork)."""

    _, snapshot_jobs, _, db = await _fresh_stores(tmp_path)
    try:
        picked = await snapshot_jobs.get_job_at("snap-x", "gnid-x")
        assert picked is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_get_job_at_regular_job_still_returned(tmp_path: Path) -> None:
    """Regression guard: a non-fan-out slot (single job, no parent/shards)
    keeps returning that job — the ORDER BY must not filter it out.
    """

    jobs_store, snapshot_jobs, workflows, db = await _fresh_stores(tmp_path)
    try:
        graph = _one_node_graph("A")
        wf = await workflows.save_draft(workflow_id=None, name="solo", graph=graph)
        snap = await workflows.create_snapshot(workflow_id=wf.workflow_id, graph=graph)

        job = Job(
            job_id="job-solo",
            snapshot_id=snap.snapshot_id,
            workflow_id=wf.workflow_id,
            graph_node_id="A",
            algorithm_name="demo",
            algorithm_version="0.1.0",
            state=JobState.DONE,
        )
        await jobs_store.create(job)

        picked = await snapshot_jobs.get_job_at(snap.snapshot_id, "A")
        assert picked == "job-solo"
    finally:
        await db.close()
