"""``latest_runs_for_{workflow,snapshot}`` prefers parent over shard on a fan-out slot.

The bug this defends against
----------------------------

A fan-out node writes one **parent** + N **shard** rows to
``snapshot_jobs``, all attributed to the same ``(snapshot_id,
graph_node_id)``. The parent's registered output handle points at the
aggregate directory (``<parent_ws>/<port>/`` with one element subdir per
shard); each shard's handle points at its own element subdir
(``<parent_ws>/<port>/<element_id>/``).

Before this fix the ORDER BY was ``s.created_ts DESC, sj.job_id DESC``
— same-snapshot rows tie on ``created_ts`` and fall through to
``sj.job_id DESC`` which is **lexicographic UUID ordering**. On a live
workflow that landed on either the parent or a random shard depending
on the luck of the UUID draw. When it landed on a shard, the frontend's
edge chip probed that shard's element dir and reported the shard-local
shape (e.g. ``dim_sizes=[21]`` for a per-cam file dir) instead of the
parent's 2-D aggregate — visible on the canvas as ``image[frame:21]``.

The tiebreaker is now
``CASE WHEN j.parent_job_id IS NULL THEN 0 ELSE 1 END`` — parents rank
above shards within the same snapshot; UUID ordering is only a
last-resort tiebreak. Analogous to
:meth:`SnapshotJobsStore.get_job_at`'s parent-first COALESCE (see
``tests/test_get_job_at_fanout.py``); different call site, same
invariant.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hololab.gateway.handle_summary import HandleSummaryCache
from hololab.gateway.handles import HandleBook
from hololab.gateway.jobs import Job, JobState
from hololab.gateway.registry import JobsStore, NodeRegistry, SnapshotJobsStore
from hololab.gateway.workflows import (
    GraphNode,
    WorkflowGraph,
    WorkflowStore,
    latest_runs_for_snapshot,
    latest_runs_for_workflow,
)
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
) -> tuple[JobsStore, SnapshotJobsStore, WorkflowStore, HandleBook, NodeRegistry, object]:
    db = await open_database(tmp_path / "hl.db")
    return (
        JobsStore(db),
        SnapshotJobsStore(db),
        WorkflowStore(db),
        HandleBook(db),
        NodeRegistry(db),
        db,
    )


async def _seed_fanout(
    *,
    jobs_store: JobsStore,
    workflows: WorkflowStore,
    workflow_id: str | None,
    gnid: str,
    parent_job_id: str,
    shard_job_ids: list[str],
) -> tuple[str, str]:
    """Attribute one parent + N shards to the same (snapshot, gnid) slot.

    Job IDs are picked by the caller so the test can force the
    worst-case UUID lex ordering (a shard's ID > parent's ID).
    """

    graph = _one_node_graph(gnid)
    wf = await workflows.save_draft(workflow_id=workflow_id, name="fan-test", graph=graph)
    snap = await workflows.create_snapshot(workflow_id=wf.workflow_id, graph=graph)

    parent = Job(
        job_id=parent_job_id,
        snapshot_id=snap.snapshot_id,
        workflow_id=wf.workflow_id,
        graph_node_id=gnid,
        algorithm_name="demo",
        algorithm_version="0.1.0",
        state=JobState.DONE,
        expected_shards=len(shard_job_ids),
    )
    await jobs_store.create(parent)

    for i, sid in enumerate(shard_job_ids):
        shard = Job(
            job_id=sid,
            snapshot_id=snap.snapshot_id,
            workflow_id=wf.workflow_id,
            graph_node_id=gnid,
            algorithm_name="demo",
            algorithm_version="0.1.0",
            state=JobState.DONE,
            parent_job_id=parent.job_id,
            shard_element_id=f"elem_{i}",
            # Newer created_ts on shards so the old SQL's ``created_ts DESC``
            # doesn't accidentally prefer the parent — the parent-first
            # rank has to be the load-bearing clause.
            created_ts=parent.created_ts + 0.001 * (i + 1),
        )
        await jobs_store.create(shard)

    return wf.workflow_id, snap.snapshot_id


@pytest.mark.asyncio
async def test_latest_runs_for_workflow_prefers_parent_over_shards(tmp_path: Path) -> None:
    """Worst-case UUID ordering: a shard's job_id lex-exceeds the parent's."""

    jobs_store, _, workflows, book, registry, db = await _fresh_stores(tmp_path)
    try:
        # ``job-parent`` < ``job-shard-*`` lexicographically → old SQL would
        # rank the last shard first via ``sj.job_id DESC``. Parent-first
        # tiebreak is the only reason the new query returns the parent.
        wf_id, _ = await _seed_fanout(
            jobs_store=jobs_store,
            workflows=workflows,
            workflow_id="wf-fan",
            gnid="fan",
            parent_job_id="job-parent",
            shard_job_ids=["job-shard-a", "job-shard-b", "job-shard-c"],
        )

        cache = HandleSummaryCache()
        picked = await latest_runs_for_workflow(
            wf_id, db=db, book=book, registry=registry, cache=cache
        )
        assert "fan" in picked, "fan-out slot must appear in latest_runs"
        assert picked["fan"]["job_id"] == "job-parent", (
            f"expected parent 'job-parent', got {picked['fan']['job_id']!r} — "
            "latest_runs_for_workflow must rank parent above shards so the "
            "canvas edge chip reads the aggregate handle, not a random "
            "shard's element dir."
        )
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_latest_runs_for_snapshot_prefers_parent_over_shards(tmp_path: Path) -> None:
    """Same tiebreak on the snapshot-scoped variant (frozen-run view)."""

    jobs_store, _, workflows, book, registry, db = await _fresh_stores(tmp_path)
    try:
        _wf_id, snap_id = await _seed_fanout(
            jobs_store=jobs_store,
            workflows=workflows,
            workflow_id="wf-fan-snap",
            gnid="fan",
            parent_job_id="job-parent",
            shard_job_ids=["job-shard-a", "job-shard-b", "job-shard-c"],
        )

        cache = HandleSummaryCache()
        graph = _one_node_graph("fan")
        picked = await latest_runs_for_snapshot(
            snap_id, graph, db=db, book=book, registry=registry, cache=cache
        )
        assert picked.get("fan", {}).get("job_id") == "job-parent", (
            f"expected parent 'job-parent' on snapshot-scoped view, got "
            f"{picked.get('fan', {}).get('job_id')!r}"
        )
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_latest_runs_for_workflow_prefers_parent_even_when_shard_newer(
    tmp_path: Path,
) -> None:
    """created_ts DESC alone would prefer a newer shard; parent-first rank wins.

    The seed above already staggered shards to have newer created_ts than
    their parent, but this call site exercises the full ORDER BY chain
    (created_ts DESC → parent-first → sj.job_id DESC) so a future
    refactor that drops the parent-first clause would fail here even if
    the UUID lex tiebreak accidentally lined up.
    """

    jobs_store, _, workflows, book, registry, db = await _fresh_stores(tmp_path)
    try:
        # Parent's job_id lex-exceeds the shards' AND has the older
        # created_ts — parent-first rank is the only clause that can
        # elect it (UUID DESC would still pick a shard whose id begins
        # with 'z').
        wf_id, _ = await _seed_fanout(
            jobs_store=jobs_store,
            workflows=workflows,
            workflow_id="wf-fan-newer-shards",
            gnid="fan",
            parent_job_id="job-parent-aaa",
            shard_job_ids=["job-shard-zzz-1", "job-shard-zzz-2"],
        )

        cache = HandleSummaryCache()
        picked = await latest_runs_for_workflow(
            wf_id, db=db, book=book, registry=registry, cache=cache
        )
        assert picked["fan"]["job_id"] == "job-parent-aaa"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_latest_runs_for_workflow_prefers_newer_parent_over_older_parent(
    tmp_path: Path,
) -> None:
    """Two parents in the same snapshot (rerun-from) — the newer parent wins.

    Concrete scenario this defends against (2026-09 classic STG run):
    a rerun-from cancelled a v0.4.0 und parent and dispatched a v0.4.1
    parent into the same snapshot. Both rows sit in ``snapshot_jobs``
    with the same ``(snapshot_id, gnid)``; both have
    ``parent_job_id IS NULL``. Without the ``j.created_ts DESC``
    tiebreak the older parent could win by UUID lex chance, so the
    edge chip surfaced a cancelled 0.4.0 handle instead of the fresh
    0.4.1 aggregate.
    """

    jobs_store, _, workflows, book, registry, db = await _fresh_stores(tmp_path)
    try:
        gnid = "reran"
        graph = _one_node_graph(gnid)
        wf = await workflows.save_draft(workflow_id="wf-rerun", name="rerun-fan", graph=graph)
        snap = await workflows.create_snapshot(workflow_id=wf.workflow_id, graph=graph)

        # Older parent gets the LEX-LARGER id so UUID DESC alone would
        # elect it — created_ts DESC has to overrule.
        older_parent = Job(
            job_id="parent-zzz-older",
            snapshot_id=snap.snapshot_id,
            workflow_id=wf.workflow_id,
            graph_node_id=gnid,
            algorithm_name="demo",
            algorithm_version="0.1.0",
            state=JobState.DONE,
            expected_shards=3,
        )
        await jobs_store.create(older_parent)

        newer_parent = Job(
            job_id="parent-aaa-newer",
            snapshot_id=snap.snapshot_id,
            workflow_id=wf.workflow_id,
            graph_node_id=gnid,
            algorithm_name="demo",
            algorithm_version="0.1.0",
            state=JobState.DONE,
            expected_shards=3,
            created_ts=older_parent.created_ts + 100.0,
        )
        await jobs_store.create(newer_parent)

        cache = HandleSummaryCache()
        picked = await latest_runs_for_workflow(
            wf.workflow_id, db=db, book=book, registry=registry, cache=cache
        )
        assert picked[gnid]["job_id"] == "parent-aaa-newer", (
            f"expected newer parent 'parent-aaa-newer', got "
            f"{picked[gnid]['job_id']!r} — a second parent generation "
            "(rerun-from) attributed to the same snapshot must win "
            "over the older parent regardless of UUID ordering."
        )
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_latest_runs_for_workflow_non_fanout_unchanged(tmp_path: Path) -> None:
    """Regression guard: a non-fan-out slot (single scalar job) still surfaces.

    The extra ORDER BY key must not filter out the sole scalar row —
    non-fan-out nodes have only a parent-like row (``parent_job_id
    IS NULL``) so the CASE evaluates to 0 for it and the row keeps
    winning as it did before.
    """

    jobs_store, _, workflows, book, registry, db = await _fresh_stores(tmp_path)
    try:
        gnid = "solo"
        graph = _one_node_graph(gnid)
        wf = await workflows.save_draft(workflow_id="wf-solo", name="solo", graph=graph)
        snap = await workflows.create_snapshot(workflow_id=wf.workflow_id, graph=graph)
        await jobs_store.create(
            Job(
                job_id="job-solo",
                snapshot_id=snap.snapshot_id,
                workflow_id=wf.workflow_id,
                graph_node_id=gnid,
                algorithm_name="demo",
                algorithm_version="0.1.0",
                state=JobState.DONE,
            )
        )

        cache = HandleSummaryCache()
        picked = await latest_runs_for_workflow(
            wf.workflow_id, db=db, book=book, registry=registry, cache=cache
        )
        assert picked.get(gnid, {}).get("job_id") == "job-solo"
    finally:
        await db.close()
