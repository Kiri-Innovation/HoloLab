"""Edge compatibility with the arrayed + arrayable + 'any' rules.

Covers the M1 slice of the arrayed<T> type-system extension:

* ``ports_compatible`` — full edge rule (tags overlap AND arrayed match).
* ``tags_compatible`` — the ``any`` wildcard short-circuits tag overlap.
* ``effective_port_arrayed`` — the ``manifest OR (arrayable AND toggle)`` rule.
* ``validate_snapshot`` — surfaces arrayed cardinality mismatches on edges.
* ``GraphNode.arrayed_toggle`` — new structural field, defaults to False,
  round-trips through JSON serialization.

Scheduler fan-out (M3) and per-node UI (M4) are separate tests.
"""

from __future__ import annotations

from hololab.gateway.workflows import (
    GraphEdge,
    GraphNode,
    InputPortView,
    OutputPortView,
    PackHandle,
    WorkflowGraph,
    effective_port_arrayed,
    graph_from_json,
    graph_to_json,
    ports_compatible,
    tags_compatible,
    validate_snapshot,
)

# ---------------------------------------------------------------------------
# Pure compatibility rules
# ---------------------------------------------------------------------------


def test_ports_compatible_scalar_to_scalar_overlap() -> None:
    assert ports_compatible(["frame_sequence"], False, ["frame_sequence"], False)


def test_ports_compatible_scalar_to_arrayed_rejected() -> None:
    assert not ports_compatible(["frame_sequence"], False, ["frame_sequence"], True)


def test_ports_compatible_arrayed_to_arrayed_overlap() -> None:
    assert ports_compatible(["video-source"], True, ["video-source"], True)


def test_ports_compatible_tag_disjoint_rejected() -> None:
    assert not ports_compatible(["frame_sequence"], False, ["colmap"], False)


def test_any_tag_matches_anything() -> None:
    assert tags_compatible(["any"], ["frame_sequence"])
    assert tags_compatible(["frame_sequence"], ["any"])
    assert tags_compatible(["any"], ["any"])
    # Wildcard still respects arrayed cardinality in ports_compatible.
    assert ports_compatible(["any"], True, ["frame_sequence"], True)
    assert not ports_compatible(["any"], False, ["frame_sequence"], True)


# ---------------------------------------------------------------------------
# effective_port_arrayed rule
# ---------------------------------------------------------------------------


def test_effective_arrayed_manifest_wins() -> None:
    # Port declared arrayed in the manifest → always arrayed regardless.
    assert effective_port_arrayed(True, False, False)
    assert effective_port_arrayed(True, True, False)


def test_effective_arrayed_toggle_flips_arrayable_pack() -> None:
    # Non-arrayed port on an arrayable pack: toggle flips it.
    assert not effective_port_arrayed(False, True, False)
    assert effective_port_arrayed(False, True, True)


def test_effective_arrayed_toggle_ignored_on_non_arrayable_pack() -> None:
    # Non-arrayable pack: toggle is meaningless, port stays as manifest says.
    assert not effective_port_arrayed(False, False, True)


# ---------------------------------------------------------------------------
# GraphNode.arrayed_toggle — structural field, default False, JSON roundtrip.
# ---------------------------------------------------------------------------


def test_graph_node_arrayed_toggle_defaults_false() -> None:
    n = GraphNode(id="n1", algorithm_name="p", algorithm_version="0.1.0")
    assert n.arrayed_toggle is False


def test_graph_node_arrayed_toggle_roundtrips() -> None:
    g = WorkflowGraph(
        nodes=[
            GraphNode(
                id="n1",
                algorithm_name="frame-extraction",
                algorithm_version="0.1.0",
                arrayed_toggle=True,
                assigned_node_id="node",
            ),
        ],
        edges=[],
    )
    reparsed = graph_from_json(graph_to_json(g))
    assert reparsed.nodes[0].arrayed_toggle is True


# ---------------------------------------------------------------------------
# validate_snapshot — arrayed cardinality is enforced on edges.
# ---------------------------------------------------------------------------


def _pk(inputs: dict, outputs: dict, arrayable: bool = False) -> PackHandle:
    return PackHandle(
        inputs={
            n: InputPortView(tags=tuple(spec["tags"]), required=True, arrayed=spec.get("arrayed", False))
            for n, spec in inputs.items()
        },
        outputs={
            n: OutputPortView(tags=tuple(spec["tags"]), arrayed=spec.get("arrayed", False))
            for n, spec in outputs.items()
        },
        arrayable=arrayable,
    )


def test_validate_flags_arrayed_scalar_edge() -> None:
    """arrayed producer → scalar consumer must be rejected."""
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
        edges=[GraphEdge(id="e", source="a", sourceHandle="o", target="b", targetHandle="i")],
    )
    packs = {
        ("src", "0.1.0"): _pk({}, {"o": {"tags": ["video-source"], "arrayed": True}}),
        ("dst", "0.1.0"): _pk({"i": {"tags": ["video-source"], "arrayed": False}}, {"out": {"tags": ["x"]}}),
    }
    issues = validate_snapshot(
        graph,
        packs_by_key=packs,
        online_node_ids={"node"},
        packs_offered_by_node={"node": {("src", "0.1.0"), ("dst", "0.1.0")}},
    )
    assert any("arrayed cardinality mismatch" in i.message for i in issues), issues


def test_validate_accepts_arrayed_arrayed_edge() -> None:
    """arrayed → arrayed on the same base tag: no issue."""
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="a", algorithm_name="src", algorithm_version="0.1.0", assigned_node_id="node"),
            GraphNode(id="b", algorithm_name="dst", algorithm_version="0.1.0", assigned_node_id="node"),
        ],
        edges=[GraphEdge(id="e", source="a", sourceHandle="o", target="b", targetHandle="i")],
    )
    packs = {
        ("src", "0.1.0"): _pk({}, {"o": {"tags": ["video-source"], "arrayed": True}}),
        ("dst", "0.1.0"): _pk({"i": {"tags": ["video-source"], "arrayed": True}}, {"out": {"tags": ["x"]}}),
    }
    issues = validate_snapshot(
        graph,
        packs_by_key=packs,
        online_node_ids={"node"},
        packs_offered_by_node={"node": {("src", "0.1.0"), ("dst", "0.1.0")}},
    )
    edge_issues = [i for i in issues if i.where.startswith("edge:")]
    assert edge_issues == [], edge_issues


def test_validate_arrayable_toggle_promotes_scalar_port_to_arrayed() -> None:
    """An arrayable consumer with the toggle on accepts an arrayed producer."""
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="a", algorithm_name="src", algorithm_version="0.1.0", assigned_node_id="node"),
            GraphNode(
                id="b",
                algorithm_name="dst",
                algorithm_version="0.1.0",
                assigned_node_id="node",
                arrayed_toggle=True,  # ← the key: promote non-arrayed ports.
            ),
        ],
        edges=[GraphEdge(id="e", source="a", sourceHandle="o", target="b", targetHandle="i")],
    )
    packs = {
        ("src", "0.1.0"): _pk({}, {"o": {"tags": ["video-source"], "arrayed": True}}),
        # dst declares its input as scalar in the manifest, but the pack
        # is arrayable — the per-node toggle should flip the port to arrayed.
        ("dst", "0.1.0"): _pk(
            {"i": {"tags": ["video-source"], "arrayed": False}},
            {"out": {"tags": ["x"]}},
            arrayable=True,
        ),
    }
    issues = validate_snapshot(
        graph,
        packs_by_key=packs,
        online_node_ids={"node"},
        packs_offered_by_node={"node": {("src", "0.1.0"), ("dst", "0.1.0")}},
    )
    edge_issues = [i for i in issues if i.where.startswith("edge:")]
    assert edge_issues == [], edge_issues


def test_validate_any_tag_wildcard_accepts_frame_sequence() -> None:
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="a", algorithm_name="src", algorithm_version="0.1.0", assigned_node_id="node"),
            GraphNode(id="b", algorithm_name="dst", algorithm_version="0.1.0", assigned_node_id="node"),
        ],
        edges=[GraphEdge(id="e", source="a", sourceHandle="o", target="b", targetHandle="i")],
    )
    packs = {
        ("src", "0.1.0"): _pk({}, {"o": {"tags": ["frame_sequence"]}}),
        # Generic utility pack accepts anything via the ``any`` wildcard.
        ("dst", "0.1.0"): _pk({"i": {"tags": ["any"]}}, {"out": {"tags": ["any"]}}),
    }
    issues = validate_snapshot(
        graph,
        packs_by_key=packs,
        online_node_ids={"node"},
        packs_offered_by_node={"node": {("src", "0.1.0"), ("dst", "0.1.0")}},
    )
    edge_issues = [i for i in issues if i.where.startswith("edge:")]
    assert edge_issues == [], edge_issues
