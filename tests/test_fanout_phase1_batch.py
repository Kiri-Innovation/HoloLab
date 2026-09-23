"""Regression tests for the batched Phase-1 of ``_execute_fanout_body``.

Phase-1 used to run one serial ``_shard_input_handles`` + ``_create_shard_row``
loop over every element, driving ~700 ``Database.write`` round-trips for a
100-shard, 3-arrayed-input merge fan-out. Measured wall-clock: ~48 s.

The new batched path (:func:`_prepare_shard_rows` + the new
:meth:`JobsStore.create_many` and :meth:`HandleBook.register_many` primitives)
collapses that to two SQL transactions. These tests lock in three properties:

1. Batch primitives write every row correctly (round-trip via ``get``);
   empty lists are safe no-ops.
2. Sub-handle & shard-row **semantics** exactly match the pre-refactor
   serial path (path derivation, tag/storage/node_id inheritance,
   ``job_id=None`` on sub-handles, scalar input pass-through, standard
   :class:`Job` fields on shard rows).
3. Order matches ``plan.element_ids``: shards line up 1:1 with the
   dispatch pool's ``asyncio.gather`` order the phase-2 loop expects.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from hololab.gateway.execution import _FanoutPlan, _prepare_shard_rows
from hololab.gateway.handles import Handle, HandleBook
from hololab.gateway.jobs import Job, JobState
from hololab.gateway.registry import JobsStore
from hololab.gateway.workflows import GraphNode
from hololab.persistence.db import open_database

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _open_stores(tmp_path: Path) -> tuple[JobsStore, HandleBook]:
    db = await open_database(tmp_path / "db.sqlite")
    return JobsStore(db), HandleBook(db)


def _job(job_id: str, *, state: JobState = JobState.PENDING) -> Job:
    return Job(
        job_id=job_id,
        workflow_id="wf",
        snapshot_id="snap",
        algorithm_name="pack",
        algorithm_version="0.1.0",
        state=state,
    )


def _handle(handle_id: str, *, path: str = "/tmp/x") -> Handle:
    return Handle(
        handle_id=handle_id,
        node_id="node-a",
        storage="dir",
        tags=["frame_sequence"],
        path=path,
        job_id=None,
        output_port_name=None,
    )


def _graph_node(**overrides) -> GraphNode:
    defaults = {
        "id": "n1",
        "algorithm_name": "pack",
        "algorithm_version": "0.1.0",
        "params": {},
        "arrayed_toggle": True,
        "parallelism": 1,
        "assigned_node_id": "node-a",
    }
    defaults.update(overrides)
    return GraphNode(**defaults)


# ---------------------------------------------------------------------------
# 1) Batch primitives — write all rows, empty is a no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jobs_store_create_many_writes_every_row(tmp_path: Path) -> None:
    store, _handles = await _open_stores(tmp_path)
    jobs = [_job(f"job-{i}") for i in range(5)]

    await store.create_many(jobs)

    for j in jobs:
        got = await store.get(j.job_id)
        assert got is not None, f"missing {j.job_id!r}"
        assert got.state is JobState.PENDING
        assert got.algorithm_name == "pack"


@pytest.mark.asyncio
async def test_jobs_store_create_many_empty_is_noop(tmp_path: Path) -> None:
    """Empty list must not touch the DB (no exception, no rows)."""
    store, _handles = await _open_stores(tmp_path)
    await store.create_many([])  # must not raise
    # Nothing to fetch; a follow-up ``get`` on any id returns None.
    assert await store.get("does-not-exist") is None


@pytest.mark.asyncio
async def test_handle_book_register_many_writes_every_row(tmp_path: Path) -> None:
    _store, book = await _open_stores(tmp_path)
    hs = [_handle(f"h-{i}", path=f"/tmp/h/{i}") for i in range(4)]

    await book.register_many(hs)

    for h in hs:
        got = await book.get(h.handle_id)
        assert got is not None
        assert got.path == h.path
        assert got.tags == ["frame_sequence"]
        assert got.storage == "dir"


@pytest.mark.asyncio
async def test_handle_book_register_many_empty_is_noop(tmp_path: Path) -> None:
    _store, book = await _open_stores(tmp_path)
    await book.register_many([])  # must not raise
    assert await book.get("does-not-exist") is None


@pytest.mark.asyncio
async def test_handle_book_register_many_upserts_on_conflict(tmp_path: Path) -> None:
    """A duplicate ``handle_id`` updates fields + clears ``deleted_ts`` —
    same ON CONFLICT semantics as per-row :meth:`register`."""
    _store, book = await _open_stores(tmp_path)
    original = _handle("h-dup", path="/tmp/original")
    await book.register(original)
    await book.mark_deleted("h-dup")
    assert (await book.get("h-dup")).deleted_ts is not None

    updated = _handle("h-dup", path="/tmp/updated")
    await book.register_many([updated])

    got = await book.get("h-dup")
    assert got is not None
    assert got.path == "/tmp/updated"
    assert got.deleted_ts is None, "re-register must clear the tombstone"


# ---------------------------------------------------------------------------
# 2) _prepare_shard_rows — sub-handle + shard-row semantics
# ---------------------------------------------------------------------------


async def _seeded_book_with_parent(tmp_path: Path) -> tuple[JobsStore, HandleBook, Handle]:
    """Seed a book with one arrayed-parent handle. Returns (store, book, parent)."""
    store, book = await _open_stores(tmp_path)
    root = tmp_path / "arr"
    root.mkdir()
    for name in ["frame_0000", "frame_0001", "frame_0002"]:
        (root / name).mkdir()
    parent = Handle(
        handle_id="h-arrayed",
        node_id="node-a",
        storage="dir",
        tags=["frame_sequence", "colmap-cams"],
        path=str(root),
        job_id=None,
        output_port_name=None,
    )
    await book.register(parent)
    return store, book, parent


def _plan(parent_handle: Handle, *, element_ids: list[str]) -> _FanoutPlan:
    """Build a minimal ``_FanoutPlan`` for the phase-1 helper.

    Only the fields ``_prepare_shard_rows`` reads are populated — the
    full plan carries more (pack_outputs, parent_ws) that phase-2 and
    node-loop code use, but phase-1 doesn't touch them.
    """
    parent_job = Job(
        job_id="parent-job-1",
        workflow_id="wf-1",
        snapshot_id="snap-1",
        algorithm_name="pack",
        algorithm_version="0.1.0",
        params={"beta": 42, "tag": "hello"},
        input_handles={"frames": parent_handle.handle_id, "config": "h-scalar-abc"},
        graph_node_id="n1",
        expected_shards=len(element_ids),
    )
    return _FanoutPlan(
        parent_job=parent_job,
        parent_ws="/tmp/ws/parent-job-1",
        element_ids=element_ids,
        arrayed_input_ports=["frames"],
        pack_outputs={},
        input_handles={"frames": parent_handle.handle_id, "config": "h-scalar-abc"},
        session_node_id="node-a",
    )


@pytest.mark.asyncio
async def test_prepare_shard_rows_produces_N_rows_in_element_order(tmp_path: Path) -> None:
    """N element_ids in, N shards out, aligned by index. This is what
    phase-2's ``asyncio.gather(*(_run_one(i, eid, s) …))`` relies on."""

    store, book, parent = await _seeded_book_with_parent(tmp_path)
    plan = _plan(parent, element_ids=["frame_0000", "frame_0001", "frame_0002"])

    shards = await _prepare_shard_rows(
        store=store,
        handles=book,
        plan=plan,
        snapshot_id="snap-1",
        workflow_id="wf-1",
        gnode=_graph_node(id="n1"),
    )
    assert [eid for _idx, eid, _s in shards] == ["frame_0000", "frame_0001", "frame_0002"]
    assert [idx for idx, _e, _s in shards] == [0, 1, 2]


@pytest.mark.asyncio
async def test_prepare_shard_rows_shard_fields_match_baseline(tmp_path: Path) -> None:
    """Shard Job carries the same fields the per-row create used to set:
    parent_job_id, shard_element_id, params (copied from parent), graph_node_id,
    algorithm identity, PENDING state, and a snapshot_id/workflow_id pair."""

    store, book, parent = await _seeded_book_with_parent(tmp_path)
    plan = _plan(parent, element_ids=["frame_0000"])

    (shards) = await _prepare_shard_rows(
        store=store,
        handles=book,
        plan=plan,
        snapshot_id="snap-1",
        workflow_id="wf-1",
        gnode=_graph_node(id="n1"),
    )
    _idx, _eid, shard = shards[0]

    fetched = await store.get(shard.job_id)
    assert fetched is not None
    assert fetched.state is JobState.PENDING
    assert fetched.parent_job_id == "parent-job-1"
    assert fetched.shard_element_id == "frame_0000"
    assert fetched.workflow_id == "wf-1"
    assert fetched.snapshot_id == "snap-1"
    assert fetched.graph_node_id == "n1"
    assert fetched.algorithm_name == "pack"
    assert fetched.algorithm_version == "0.1.0"
    # Params inherit from the parent verbatim (not a shared dict — mutating
    # the shard's copy must not mutate the parent's).
    assert fetched.params == {"beta": 42, "tag": "hello"}
    fetched.params["beta"] = 999
    assert plan.parent_job.params == {"beta": 42, "tag": "hello"}


@pytest.mark.asyncio
async def test_prepare_shard_rows_subhandle_semantics(tmp_path: Path) -> None:
    """For each arrayed input port and each element, a synthetic sub-handle
    is registered that:
      - has a fresh UUID (not the parent's id),
      - path = parent.path / element_id,
      - inherits parent tags + storage (not a shared reference — tag list
        must be independent so mutation doesn't ripple),
      - node_id = plan.session_node_id (the producer),
      - job_id = None (transient, unattributed).
    Scalar (non-arrayed) inputs pass through unchanged."""

    store, book, parent = await _seeded_book_with_parent(tmp_path)
    plan = _plan(parent, element_ids=["frame_0000", "frame_0001"])

    shards = await _prepare_shard_rows(
        store=store,
        handles=book,
        plan=plan,
        snapshot_id="snap-1",
        workflow_id="wf-1",
        gnode=_graph_node(id="n1"),
    )
    for _idx, element_id, shard in shards:
        # Scalar input passes through.
        assert shard.input_handles["config"] == "h-scalar-abc"
        # Arrayed input got a new sub-handle.
        sub_id = shard.input_handles["frames"]
        assert sub_id != parent.handle_id
        sub = await book.get(sub_id)
        assert sub is not None
        assert sub.path == str(Path(parent.path) / element_id)
        assert sub.tags == parent.tags
        assert sub.tags is not parent.tags, "tag list must be a copy, not aliased"
        assert sub.storage == parent.storage
        assert sub.node_id == "node-a"
        assert sub.job_id is None


@pytest.mark.asyncio
async def test_prepare_shard_rows_raises_when_arrayed_parent_missing(
    tmp_path: Path,
) -> None:
    """A dangling handle_id on an arrayed port must surface as
    :class:`WorkflowRunError` — same error surface as the pre-refactor
    per-shard path, otherwise callers that inspect the message break."""

    from hololab.gateway.execution import WorkflowRunError

    store, book = await _open_stores(tmp_path)
    # Build a plan whose arrayed port references a handle that was never
    # registered.
    fake_parent = Handle(
        handle_id="ghost",
        node_id="node-a",
        storage="dir",
        tags=[],
        path="/nowhere",
    )
    plan = _plan(fake_parent, element_ids=["e0"])

    with pytest.raises(WorkflowRunError, match="not registered"):
        await _prepare_shard_rows(
            store=store,
            handles=book,
            plan=plan,
            snapshot_id="snap-1",
            workflow_id="wf-1",
            gnode=_graph_node(id="n1"),
        )


@pytest.mark.asyncio
async def test_prepare_shard_rows_empty_element_list_returns_empty(
    tmp_path: Path,
) -> None:
    """A zero-shard fan-out isn't a normal state (``_discover_element_ids``
    guards against it) but the helper must be robust: no elements → no
    shards, no DB writes. Guards against accidental infinite work if a
    future refactor slips a bad plan through the door."""

    store, book, parent = await _seeded_book_with_parent(tmp_path)
    plan = _plan(parent, element_ids=[])

    shards = await _prepare_shard_rows(
        store=store,
        handles=book,
        plan=plan,
        snapshot_id="snap-1",
        workflow_id="wf-1",
        gnode=_graph_node(id="n1"),
    )
    assert shards == []


# ---------------------------------------------------------------------------
# 3) Batch flush: one Database.write per primitive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prepare_shard_rows_uses_exactly_two_db_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Perf regression guard — the whole point of this refactor is that
    N shards translate to O(1) writer-loop trips, not O(N). We wrap
    ``Database.write`` and count invocations across the whole helper:
    handle prefetch (reads, not counted) + register_many + create_many
    == 2 writes total. If a future edit reintroduces a per-shard
    ``store.create`` or ``handles.register`` inside the loop this test
    breaks loudly."""

    store, book, parent = await _seeded_book_with_parent(tmp_path)
    plan = _plan(parent, element_ids=[f"e{i}" for i in range(10)])

    # Wrap the underlying Database.write to count how many times phase-1
    # hits the writer loop. Both stores share the same Database instance.
    counter = MagicMock()
    real_write = store._db.write

    async def counting_write(fn):
        counter()
        return await real_write(fn)

    monkeypatch.setattr(store._db, "write", counting_write)

    await _prepare_shard_rows(
        store=store,
        handles=book,
        plan=plan,
        snapshot_id="snap-1",
        workflow_id="wf-1",
        gnode=_graph_node(id="n1"),
    )
    assert counter.call_count == 2, (
        f"phase-1 must issue exactly two batched writes for 10 shards; got {counter.call_count}"
    )
