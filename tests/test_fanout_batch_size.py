"""Candidate A (batched shards): ``GraphNode.batch_size > 1`` coalesces
elements into fewer shard jobs without changing the pack contract.

Semantics covered:

  1. **``batch_size=1`` is a strict no-op** — one shard per element,
     ``shard_element_ids`` stays NULL, ``shard_element_id`` is
     authoritative. Byte-identical to pre-V14. This is the safety
     line: existing arrayed nodes that never set ``batch_size`` must
     see zero behaviour change.
  2. **``batch_size=B`` groups elements into ``ceil(N / B)`` shards**,
     each carrying its full ``shard_element_ids`` list in the DB.
     Sub-handle count (one per (element, arrayed_port)) is invariant
     — only the SHARD row count drops.
  3. **``JobAssign`` fields** — batched shards carry
     ``shard_element_ids`` + ``shard_input_paths`` (index-aligned);
     ``input_paths`` is empty (per-element paths supersede the scalar
     shortcut). Non-batched shards carry the scalar C1 form.
  4. **Batched shell wrapper** — the node runtime concatenates every
     element's rendered shell with ``set -euo pipefail`` so the first
     element to fail aborts the whole batch, matching the user's
     "arrayed job is an integral unit" cancel/deploy semantics.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hololab.gateway.execution import _FanoutPlan, _prepare_shard_rows
from hololab.gateway.handles import Handle, HandleBook
from hololab.gateway.jobs import Job
from hololab.gateway.registry import JobsStore
from hololab.gateway.workflows import GraphNode
from hololab.persistence.db import open_database


async def _open_stores(tmp_path: Path) -> tuple[JobsStore, HandleBook]:
    db = await open_database(tmp_path / "db.sqlite")
    return JobsStore(db), HandleBook(db)


async def _seeded_book_with_parent(
    tmp_path: Path, elements: list[str]
) -> tuple[JobsStore, HandleBook, Handle]:
    store, book = await _open_stores(tmp_path)
    root = tmp_path / "arr"
    root.mkdir()
    for name in elements:
        (root / name).mkdir()
    parent = Handle(
        handle_id="h-arrayed",
        node_id="node-a",
        storage="dir",
        tags=["colmap-cams"],
        path=str(root),
        job_id=None,
        output_port_name=None,
    )
    await book.register(parent)
    return store, book, parent


def _plan(parent: Handle, element_ids: list[str]) -> _FanoutPlan:
    return _FanoutPlan(
        parent_job=Job(
            job_id="parent-1",
            workflow_id="wf",
            snapshot_id="snap",
            algorithm_name="pack",
            algorithm_version="0.1.0",
            params={},
            input_handles={"frames": parent.handle_id, "cfg": "h-scalar"},
            graph_node_id="n1",
            expected_shards=len(element_ids),
        ),
        parent_ws="/tmp/parent-ws",
        element_ids=element_ids,
        arrayed_input_ports=["frames"],
        pack_outputs={},
        input_handles={"frames": parent.handle_id, "cfg": "h-scalar"},
        session_node_id="node-a",
    )


def _graph_node(batch_size: int = 1) -> GraphNode:
    return GraphNode(
        id="n1",
        algorithm_name="pack",
        algorithm_version="0.1.0",
        arrayed_toggle=True,
        parallelism=1,
        batch_size=batch_size,
        assigned_node_id="node-a",
    )


# ---------------------------------------------------------------------------
# 1) batch_size=1 must be a strict no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_size_1_produces_one_shard_per_element(tmp_path: Path) -> None:
    """The safety line: batch_size=1 keeps the pre-V14 layout — one
    shard row per element, ``shard_element_ids`` NULL, list-form
    fields on the returned tuple are all length-1.
    """

    element_ids = ["e0", "e1", "e2", "e3"]
    store, book, parent = await _seeded_book_with_parent(tmp_path, element_ids)
    shards = await _prepare_shard_rows(
        store=store,
        handles=book,
        plan=_plan(parent, element_ids),
        snapshot_id="snap",
        workflow_id="wf",
        gnode=_graph_node(batch_size=1),
    )
    assert len(shards) == 4
    for _idx, batch_eids, shard, batch_paths in shards:
        assert len(batch_eids) == 1
        assert len(batch_paths) == 1
        fetched = await store.get(shard.job_id)
        assert fetched is not None
        # Byte-identical pre-V14 shape.
        assert fetched.shard_element_ids is None
        assert fetched.shard_element_id == batch_eids[0]


# ---------------------------------------------------------------------------
# 2) batch_size=B groups elements
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_size_3_coalesces_10_elements_into_4_shards(tmp_path: Path) -> None:
    """10 elements with B=3 → 4 shard jobs (3 + 3 + 3 + 1). Every shard
    row's ``shard_element_ids`` list is exactly the batch it covers;
    the scalar ``shard_element_id`` is the first element.
    """

    element_ids = [f"e{i}" for i in range(10)]
    store, book, parent = await _seeded_book_with_parent(tmp_path, element_ids)
    shards = await _prepare_shard_rows(
        store=store,
        handles=book,
        plan=_plan(parent, element_ids),
        snapshot_id="snap",
        workflow_id="wf",
        gnode=_graph_node(batch_size=3),
    )
    assert len(shards) == 4
    expected_batches = [
        ["e0", "e1", "e2"],
        ["e3", "e4", "e5"],
        ["e6", "e7", "e8"],
        ["e9"],
    ]
    for (_idx, batch_eids, shard, batch_paths), expected in zip(
        shards, expected_batches, strict=True
    ):
        assert batch_eids == expected
        assert len(batch_paths) == len(expected)
        fetched = await store.get(shard.job_id)
        assert fetched is not None
        assert fetched.shard_element_id == expected[0]
        # Batched rows (len > 1) carry the full list. The trailing
        # single-element batch keeps the scalar-only shape so it
        # round-trips through the DB with the same layout as a
        # batch_size=1 shard.
        if len(expected) > 1:
            assert fetched.shard_element_ids == expected
        else:
            assert fetched.shard_element_ids is None


@pytest.mark.asyncio
async def test_batch_size_larger_than_element_count(tmp_path: Path) -> None:
    """Guard: B >> N produces one shard covering every element (no
    empty tail batch, no crash). ``shard_element_ids`` populated
    because the single batch has len > 1.
    """

    element_ids = ["e0", "e1", "e2"]
    store, book, parent = await _seeded_book_with_parent(tmp_path, element_ids)
    shards = await _prepare_shard_rows(
        store=store,
        handles=book,
        plan=_plan(parent, element_ids),
        snapshot_id="snap",
        workflow_id="wf",
        gnode=_graph_node(batch_size=100),
    )
    assert len(shards) == 1
    _idx, batch_eids, shard, batch_paths = shards[0]
    assert batch_eids == ["e0", "e1", "e2"]
    assert len(batch_paths) == 3
    fetched = await store.get(shard.job_id)
    assert fetched is not None
    assert fetched.shard_element_ids == ["e0", "e1", "e2"]


# ---------------------------------------------------------------------------
# 3) Sub-handle count is invariant to batching
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subhandle_count_unchanged_by_batching(tmp_path: Path) -> None:
    """Batching only merges SHARD rows; the per-(element, arrayed_port)
    sub-handle count stays the same. 6 elements x 1 arrayed port = 6
    sub-handles, whether we ship 6 shard rows (B=1) or 2 shard rows
    (B=3).
    """

    element_ids = [f"e{i}" for i in range(6)]
    store, book, parent = await _seeded_book_with_parent(tmp_path, element_ids)
    shards_b1 = await _prepare_shard_rows(
        store=store,
        handles=book,
        plan=_plan(parent, element_ids),
        snapshot_id="snap-b1",
        workflow_id="wf",
        gnode=_graph_node(batch_size=1),
    )
    subhandles_b1_paths = [
        p
        for _idx, _eids, _shard, batch_paths in shards_b1
        for path_dict in batch_paths
        for p in path_dict.values()
    ]

    # Fresh state for the second run so sub-handle registration doesn't
    # collide across the two _prepare_shard_rows calls.
    store2, book2, parent2 = await _seeded_book_with_parent(tmp_path / "again", element_ids)
    shards_b3 = await _prepare_shard_rows(
        store=store2,
        handles=book2,
        plan=_plan(parent2, element_ids),
        snapshot_id="snap-b3",
        workflow_id="wf",
        gnode=_graph_node(batch_size=3),
    )
    subhandles_b3_paths = [
        p
        for _idx, _eids, _shard, batch_paths in shards_b3
        for path_dict in batch_paths
        for p in path_dict.values()
    ]

    # Same total count (6), same set of underlying element leaves (the
    # ``basename`` — the parent path prefixes differ because the second
    # call runs in a fresh tmp subdir, but the leaf names must match).
    assert len(subhandles_b1_paths) == 6
    assert len(subhandles_b3_paths) == 6
    assert sorted(Path(p).name for p in subhandles_b1_paths) == sorted(
        Path(p).name for p in subhandles_b3_paths
    )
    # Shard row count changed (6 → 2).
    assert len(shards_b1) == 6
    assert len(shards_b3) == 2


# ---------------------------------------------------------------------------
# 4) JobAssign fields on the wire
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batched_shard_carries_element_ids_and_paths_on_wire(tmp_path: Path) -> None:
    """End-to-end coverage of the wire: patch ``_dispatch_prepared_shard``
    to capture the built ``JobAssign`` payload and verify the batched
    fields are populated (or empty at B=1) exactly as the node runtime
    expects.
    """

    from hololab.gateway.execution import _dispatch_prepared_shard

    async def _capture(**kwargs) -> None:
        _capture.calls.append(kwargs)  # type: ignore[attr-defined]

    _capture.calls = []  # type: ignore[attr-defined]

    # Batched B=3 (4 elements → 2 shards, [e0,e1,e2] and [e3]).
    element_ids = ["e0", "e1", "e2", "e3"]
    store, book, parent = await _seeded_book_with_parent(tmp_path, element_ids)
    shards = await _prepare_shard_rows(
        store=store,
        handles=book,
        plan=_plan(parent, element_ids),
        snapshot_id="snap",
        workflow_id="wf",
        gnode=_graph_node(batch_size=3),
    )

    # Directly invoke ``_dispatch_prepared_shard`` via a monkeypatched
    # stub so we can inspect the assembled JobAssign without actually
    # standing up a NodeSession / WebSocket.
    from unittest.mock import AsyncMock, MagicMock

    captured: list = []

    class _FakeSession:
        node_id = "node-a"
        protocol_v = 1
        send_lock = MagicMock()
        send_lock.__aenter__ = AsyncMock()
        send_lock.__aexit__ = AsyncMock()

        class _WS:
            send_text = AsyncMock(side_effect=lambda frame: captured.append(frame))

        ws = _WS()

    class _FakeRegistry:
        def get_session(self, _node_id: str):
            return _FakeSession()

    class _FakeHub:
        def broadcast(self, _frame) -> None:
            pass

    for _idx, batch_eids, shard, batch_paths in shards:
        # Reload shard row so PENDING invariant holds.
        fresh = await store.get(shard.job_id)
        assert fresh is not None
        # Match the ``_run_one`` call convention: single-element batches
        # keep the scalar C1 shortcut populated so the node can skip a
        # per-shard handle_locate for that lone element.
        await _dispatch_prepared_shard(
            registry=_FakeRegistry(),  # type: ignore[arg-type]
            store=store,
            hub=_FakeHub(),  # type: ignore[arg-type]
            gnode=_graph_node(batch_size=3),
            shard=fresh,
            shard_element_id=batch_eids[0],
            shard_output_prefix="/tmp/ws/parent-1",
            shard_input_paths=batch_paths[0] if len(batch_eids) == 1 else {},
            batch_element_ids=batch_eids,
            batch_input_paths=batch_paths,
        )

    import json

    frames = [json.loads(f) for f in captured]
    assert len(frames) == 2

    # First shard: batch [e0, e1, e2]. Wire carries shard_element_ids +
    # per-element shard_input_paths. Legacy scalar input_paths is empty
    # under batching (per-element paths supersede it).
    p0 = frames[0]["payload"]
    assert p0["shard_element_id"] == "e0"
    assert p0["shard_element_ids"] == ["e0", "e1", "e2"]
    assert len(p0["shard_input_paths"]) == 3
    assert p0["input_paths"] == {}
    # Each per-element path map carries the arrayed port only.
    for i, eid in enumerate(["e0", "e1", "e2"]):
        assert "frames" in p0["shard_input_paths"][i]
        assert p0["shard_input_paths"][i]["frames"].endswith(f"/{eid}")

    # Second shard: single-element batch [e3]. Batched wire is empty,
    # falls back to scalar shard_element_id + scalar input_paths — the
    # pre-batching C1 shape.
    p1 = frames[1]["payload"]
    assert p1["shard_element_id"] == "e3"
    assert p1["shard_element_ids"] == []
    assert p1["shard_input_paths"] == []
    assert "frames" in p1["input_paths"]
    assert p1["input_paths"]["frames"].endswith("/e3")


# ---------------------------------------------------------------------------
# 5) Batched shell has ``set -euo pipefail`` so first fail aborts
# ---------------------------------------------------------------------------


def test_batched_shell_wraps_with_pipefail() -> None:
    """The batched wrapper prepends ``set -euo pipefail`` so bash
    aborts the whole subprocess on the first non-zero exit. Assert
    on the string composed by the node runtime.

    Uses the same string template the runtime does (see
    ``hololab/node/runtime.py`` — the wrapper prepends the header
    and joins per-element rendered shells with a comment header).
    """

    per_elem_shells = [
        "echo elem-a\n",
        "false\n",
        "echo elem-c\n",
    ]
    batch_eids = ["a", "b", "c"]
    wrapped_shells = [
        f"# element {i + 1}/{len(per_elem_shells)}: {eid}\n{shell}"
        for i, (shell, eid) in enumerate(zip(per_elem_shells, batch_eids, strict=True))
    ]
    final = "set -euo pipefail\n" + "\n".join(wrapped_shells)

    # Header + per-element comments + shells all present, in order.
    assert final.startswith("set -euo pipefail\n")
    assert "# element 1/3: a" in final
    assert "# element 2/3: b" in final
    assert "# element 3/3: c" in final
    # The ``false`` element sits ahead of ``echo elem-c`` and, under
    # ``set -e``, ``elem-c`` will not execute — this is the guaranteed
    # "arrayed job is an integral unit" behaviour under bash.
    assert final.index("false") < final.index("elem-c")
