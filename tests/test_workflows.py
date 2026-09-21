"""Workflow graph model + persistence + topological sort + validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from hololab.gateway.workflows import (
    GraphCycle,
    GraphEdge,
    GraphNode,
    InputPortView,
    OutputPortView,
    PackHandle,
    WorkflowConflict,
    WorkflowGraph,
    WorkflowStore,
    graph_from_json,
    graph_to_json,
    tags_compatible,
    topological_order,
    validate_snapshot,
)
from hololab.persistence.db import open_database

# ---------------------------------------------------------------------------
# Graph shape + serialization
# ---------------------------------------------------------------------------


def test_graph_roundtrips_through_json() -> None:
    g = WorkflowGraph(
        nodes=[
            GraphNode(
                id="n1",
                algorithm_name="demo-echo",
                algorithm_version="0.1.0",
                params={"iterations": 5},
                assigned_node_id="node-uuid",
            ),
        ],
        edges=[],
    )
    reparsed = graph_from_json(graph_to_json(g))
    assert reparsed == g


def test_tags_compatible_intersection() -> None:
    assert tags_compatible(["a", "b"], ["b"])
    assert not tags_compatible(["a"], ["b"])
    assert not tags_compatible([], ["a"])
    assert not tags_compatible([], [])


# ---------------------------------------------------------------------------
# Topological sort
# ---------------------------------------------------------------------------


def _linear_chain(n: int) -> WorkflowGraph:
    nodes = [GraphNode(id=f"n{i}", algorithm_name="p", algorithm_version="0.1.0") for i in range(n)]
    edges = [
        GraphEdge(
            id=f"e{i}", source=f"n{i}", sourceHandle="o", target=f"n{i + 1}", targetHandle="i"
        )
        for i in range(n - 1)
    ]
    return WorkflowGraph(nodes=nodes, edges=edges)


def test_topological_linear_chain() -> None:
    g = _linear_chain(4)
    assert topological_order(g) == ["n0", "n1", "n2", "n3"]


def test_topological_deterministic_tiebreak() -> None:
    # Two isolated islands; Kahn's algorithm with sort-by-id gives a stable order.
    g = WorkflowGraph(
        nodes=[
            GraphNode(id="b", algorithm_name="p", algorithm_version="0.1.0"),
            GraphNode(id="a", algorithm_name="p", algorithm_version="0.1.0"),
            GraphNode(id="c", algorithm_name="p", algorithm_version="0.1.0"),
        ],
        edges=[],
    )
    assert topological_order(g) == ["a", "b", "c"]


def test_topological_rejects_cycle() -> None:
    g = WorkflowGraph(
        nodes=[
            GraphNode(id="a", algorithm_name="p", algorithm_version="0.1.0"),
            GraphNode(id="b", algorithm_name="p", algorithm_version="0.1.0"),
        ],
        edges=[
            GraphEdge(id="e1", source="a", sourceHandle="o", target="b", targetHandle="i"),
            GraphEdge(id="e2", source="b", sourceHandle="o", target="a", targetHandle="i"),
        ],
    )
    with pytest.raises(GraphCycle):
        topological_order(g)


# ---------------------------------------------------------------------------
# Snapshot validation
# ---------------------------------------------------------------------------


def _pack(inputs=None, outputs=None, *, arrayable: bool = False) -> PackHandle:
    """Build a PackHandle from human-friendly kwargs.

    ``inputs``  values: ``(tags, required=True)`` or plain ``tags`` list.
    ``outputs`` values: plain ``tags`` list.
    """

    def _norm_in(v) -> InputPortView:
        if isinstance(v, tuple):
            tags, required = v
            return InputPortView(tags=tuple(tags), required=bool(required), arrayed=False)
        return InputPortView(tags=tuple(v), required=True, arrayed=False)

    def _norm_out(v) -> OutputPortView:
        if isinstance(v, tuple):
            (tags,) = v
            return OutputPortView(tags=tuple(tags), arrayed=False)
        return OutputPortView(tags=tuple(v), arrayed=False)

    return PackHandle(
        inputs={k: _norm_in(v) for k, v in (inputs or {}).items()},
        outputs={k: _norm_out(v) for k, v in (outputs or {}).items()},
        arrayable=arrayable,
    )


def test_validate_flags_missing_assignment() -> None:
    graph = WorkflowGraph(
        nodes=[GraphNode(id="n1", algorithm_name="p", algorithm_version="0.1.0")], edges=[]
    )
    issues = validate_snapshot(
        graph,
        packs_by_key={("p", "0.1.0"): _pack(outputs={"o": ["t"]})},
        online_node_ids={"node-a"},
        packs_offered_by_node={"node-a": {("p", "0.1.0")}},
    )
    assert any("assigned_node_id is required" in i.message for i in issues)


def test_validate_flags_tag_mismatch() -> None:
    graph = WorkflowGraph(
        nodes=[
            GraphNode(
                id="a",
                algorithm_name="src",
                algorithm_version="0.1.0",
                assigned_node_id="node",
            ),
            GraphNode(
                id="b",
                algorithm_name="dst",
                algorithm_version="0.1.0",
                assigned_node_id="node",
            ),
        ],
        edges=[
            GraphEdge(id="e", source="a", sourceHandle="o", target="b", targetHandle="i"),
        ],
    )
    packs = {
        ("src", "0.1.0"): _pack(outputs={"o": ["x"]}),
        # target port needs tag ``y`` but is required (no wire from x-tagged source)
        ("dst", "0.1.0"): _pack(inputs={"i": (["y"], True)}),
    }
    issues = validate_snapshot(
        graph,
        packs_by_key=packs,
        online_node_ids={"node"},
        packs_offered_by_node={"node": {("src", "0.1.0"), ("dst", "0.1.0")}},
    )
    assert any("tag mismatch" in i.message for i in issues)


def test_validate_flags_unwired_required_input() -> None:
    graph = WorkflowGraph(
        nodes=[
            GraphNode(
                id="b", algorithm_name="dst", algorithm_version="0.1.0", assigned_node_id="node"
            ),
        ],
        edges=[],
    )
    packs = {("dst", "0.1.0"): _pack(inputs={"i": (["y"], True)})}
    issues = validate_snapshot(
        graph,
        packs_by_key=packs,
        online_node_ids={"node"},
        packs_offered_by_node={"node": {("dst", "0.1.0")}},
    )
    assert any("required input 'i'" in i.message for i in issues)


def test_validate_optional_input_may_be_unwired() -> None:
    graph = WorkflowGraph(
        nodes=[
            GraphNode(
                id="b", algorithm_name="dst", algorithm_version="0.1.0", assigned_node_id="node"
            ),
        ],
        edges=[],
    )
    packs = {
        ("dst", "0.1.0"): _pack(
            inputs={"i": (["y"], False)},  # optional
            outputs={"o": ["y"]},
        )
    }
    issues = validate_snapshot(
        graph,
        packs_by_key=packs,
        online_node_ids={"node"},
        packs_offered_by_node={"node": {("dst", "0.1.0")}},
    )
    assert issues == []


def test_validate_accepts_healthy_two_node_chain() -> None:
    graph = WorkflowGraph(
        nodes=[
            GraphNode(
                id="a", algorithm_name="src", algorithm_version="0.1.0", assigned_node_id="node"
            ),
            GraphNode(
                id="b", algorithm_name="dst", algorithm_version="0.1.0", assigned_node_id="node"
            ),
        ],
        edges=[
            GraphEdge(id="e", source="a", sourceHandle="o", target="b", targetHandle="i"),
        ],
    )
    packs = {
        ("src", "0.1.0"): _pack(outputs={"o": ["t"]}),
        ("dst", "0.1.0"): _pack(
            inputs={"i": (["t"], True)},
            outputs={"o2": ["t2"]},
        ),
    }
    issues = validate_snapshot(
        graph,
        packs_by_key=packs,
        online_node_ids={"node"},
        packs_offered_by_node={"node": {("src", "0.1.0"), ("dst", "0.1.0")}},
    )
    assert issues == []


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


async def test_workflow_store_roundtrip(tmp_path: Path) -> None:
    db = await open_database(tmp_path / "wf.sqlite")
    try:
        store = WorkflowStore(db)
        graph = _linear_chain(2)

        row = await store.save_draft(workflow_id=None, name="w1", graph=graph)
        assert row.name == "w1"
        assert row.workflow_id

        again = await store.get_draft(row.workflow_id)
        assert again is not None
        assert again.graph == graph

        # Upsert updates the name + graph in place.
        row2 = await store.save_draft(workflow_id=row.workflow_id, name="renamed", graph=graph)
        assert row2.workflow_id == row.workflow_id
        assert row2.name == "renamed"

        # Snapshot exists as an immutable copy.
        snap = await store.create_snapshot(workflow_id=row.workflow_id, graph=graph)
        assert snap.snapshot_id
        again_snap = await store.get_snapshot(snap.snapshot_id)
        assert again_snap is not None
        assert again_snap.graph == graph

        # Draft list is queryable.
        drafts = await store.list_drafts()
        assert any(d["workflow_id"] == row.workflow_id for d in drafts)

        # Delete removes the draft (snapshots cascade).
        await store.delete_draft(row.workflow_id)
        assert await store.get_draft(row.workflow_id) is None
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Optimistic-lock on save_draft — the "stale tab clobbers a concurrent
# writer's edits" bug this closes.
# ---------------------------------------------------------------------------


async def test_save_draft_accepts_matching_base_updated_ts(tmp_path: Path) -> None:
    """A save whose ``base_updated_ts`` matches the persisted row proceeds."""
    db = await open_database(tmp_path / "wf.sqlite")
    try:
        store = WorkflowStore(db)
        graph = _linear_chain(2)

        first = await store.save_draft(workflow_id=None, name="w", graph=graph)
        # Load the row's ts as the "base" a caller would remember.
        second = await store.save_draft(
            workflow_id=first.workflow_id,
            name="w-renamed",
            graph=graph,
            base_updated_ts=first.updated_ts,
        )
        assert second.name == "w-renamed"
        assert second.updated_ts >= first.updated_ts
    finally:
        await db.close()


async def test_save_draft_rejects_stale_base_updated_ts(tmp_path: Path) -> None:
    """A stale ``base_updated_ts`` (someone else bumped the row) → conflict."""
    db = await open_database(tmp_path / "wf.sqlite")
    try:
        store = WorkflowStore(db)
        graph = _linear_chain(2)

        first = await store.save_draft(workflow_id=None, name="w", graph=graph)
        # Simulate another writer bumping the row.
        _bumped = await store.save_draft(
            workflow_id=first.workflow_id,
            name="w-by-other",
            graph=graph,
        )
        # First client still holds the pre-bump ts — attempting to save
        # with it must raise WorkflowConflict carrying the current row.
        with pytest.raises(WorkflowConflict) as ex:
            await store.save_draft(
                workflow_id=first.workflow_id,
                name="w-by-stale-tab",
                graph=graph,
                base_updated_ts=first.updated_ts,
            )
        assert ex.value.current.name == "w-by-other"
        assert ex.value.base_updated_ts == first.updated_ts
        # The stale save must not have overwritten the row.
        current = await store.get_draft(first.workflow_id)
        assert current is not None
        assert current.name == "w-by-other"
    finally:
        await db.close()


async def test_save_draft_without_base_ts_falls_through(tmp_path: Path) -> None:
    """Legacy path: no ``base_updated_ts`` → last-write-wins (unchanged).

    Kept as a compatibility hatch for scripts / agents that don't yet
    thread the guard through. The server logs a warning, but the write
    proceeds so old clients don't hard-fail on the rollout.
    """
    db = await open_database(tmp_path / "wf.sqlite")
    try:
        store = WorkflowStore(db)
        graph = _linear_chain(2)

        first = await store.save_draft(workflow_id=None, name="w", graph=graph)
        # No base_updated_ts — legacy last-write-wins, no conflict.
        second = await store.save_draft(
            workflow_id=first.workflow_id,
            name="w-legacy",
            graph=graph,
        )
        assert second.name == "w-legacy"
    finally:
        await db.close()


async def test_save_draft_base_ts_ignored_for_new_workflow(tmp_path: Path) -> None:
    """``base_updated_ts`` on a workflow_id that doesn't exist yet is a no-op.

    Two clients minting a NEW workflow can't conflict (they have distinct
    server-generated ids); a client that provides a workflow_id AND a
    base_ts but no row exists yet is treated as "first save", not a
    conflict — matches the "workflow_id was minted here but not saved
    yet" edge case (e.g. a page reload between mint and first save).
    """
    db = await open_database(tmp_path / "wf.sqlite")
    try:
        store = WorkflowStore(db)
        graph = _linear_chain(2)
        row = await store.save_draft(
            workflow_id="00000000-0000-0000-0000-000000000000",
            name="w",
            graph=graph,
            base_updated_ts=123.456,  # never existed; no conflict
        )
        assert row.workflow_id == "00000000-0000-0000-0000-000000000000"
    finally:
        await db.close()
