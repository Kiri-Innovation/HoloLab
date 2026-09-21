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


def test_effective_arrayed_scalar_output_of_fanout_node_aggregates() -> None:
    # ``scalar: true`` on an output port of a fan-out node
    # (arrayable + toggle on) aggregates to arrayed<T> at the parent job.
    # The runtime already treats this port as arrayed downstream
    # (execution._execute_fanout_body registers a parent handle whose
    # path is ``{parent_ws}/{port}/`` populated with one subdir per
    # shard); the validator must agree.
    assert effective_port_arrayed(False, True, True, port_scalar=True, is_output=True)
    # Same port on a fan-out node with toggle off is a single scalar
    # invocation — non-arrayed, no aggregation.
    assert not effective_port_arrayed(False, True, False, port_scalar=True, is_output=True)
    # Non-arrayable pack: no shards, no aggregation, scalar stays scalar.
    assert not effective_port_arrayed(False, False, True, port_scalar=True, is_output=True)


def test_effective_arrayed_scalar_input_always_broadcasts() -> None:
    # ``scalar: true`` on an input port is always non-arrayed regardless
    # of pack arrayability — that's the broadcast contract for cams-style
    # scalar bundles fed into every fan-out shard.
    assert not effective_port_arrayed(False, True, True, port_scalar=True)
    assert not effective_port_arrayed(True, True, True, port_scalar=True)


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
            n: InputPortView(
                tags=tuple(spec["tags"]),
                required=True,
                arrayed=spec.get("arrayed", False),
                scalar=spec.get("scalar", False),
            )
            for n, spec in inputs.items()
        },
        outputs={
            n: OutputPortView(
                tags=tuple(spec["tags"]),
                arrayed=spec.get("arrayed", False),
                scalar=spec.get("scalar", False),
            )
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
        ("dst", "0.1.0"): _pk(
            {"i": {"tags": ["video-source"], "arrayed": False}}, {"out": {"tags": ["x"]}}
        ),
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
            GraphNode(
                id="a", algorithm_name="src", algorithm_version="0.1.0", assigned_node_id="node"
            ),
            GraphNode(
                id="b", algorithm_name="dst", algorithm_version="0.1.0", assigned_node_id="node"
            ),
        ],
        edges=[GraphEdge(id="e", source="a", sourceHandle="o", target="b", targetHandle="i")],
    )
    packs = {
        ("src", "0.1.0"): _pk({}, {"o": {"tags": ["video-source"], "arrayed": True}}),
        ("dst", "0.1.0"): _pk(
            {"i": {"tags": ["video-source"], "arrayed": True}}, {"out": {"tags": ["x"]}}
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


def test_validate_arrayable_toggle_promotes_scalar_port_to_arrayed() -> None:
    """An arrayable consumer with the toggle on accepts an arrayed producer."""
    graph = WorkflowGraph(
        nodes=[
            GraphNode(
                id="a", algorithm_name="src", algorithm_version="0.1.0", assigned_node_id="node"
            ),
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


def test_scalar_output_to_arrayed_input_rejected() -> None:
    """scalar: true output → arrayed input must be rejected with a specific message."""
    graph = WorkflowGraph(
        nodes=[
            GraphNode(
                id="a", algorithm_name="src", algorithm_version="0.1.0", assigned_node_id="node"
            ),
            GraphNode(
                id="b",
                algorithm_name="dst",
                algorithm_version="0.1.0",
                assigned_node_id="node",
                arrayed_toggle=True,
            ),
        ],
        edges=[GraphEdge(id="e", source="a", sourceHandle="o", target="b", targetHandle="i")],
    )
    packs = {
        ("src", "0.1.0"): _pk({}, {"o": {"tags": ["colmap-cameras-txt"], "scalar": True}}),
        ("dst", "0.1.0"): _pk(
            {"i": {"tags": ["colmap-cameras-txt"], "arrayed": False}},
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
    assert edge_issues, "expected validation issue for scalar output → arrayed input"
    assert any("scalar output" in i.message for i in edge_issues), edge_issues


def test_fanout_scalar_output_to_arrayed_input_allowed() -> None:
    """Fan-out node's ``scalar: true`` output aggregates to arrayed<T> at the
    parent job — the framework registers a parent handle whose path is a
    directory of per-shard subdirs. The validator must accept an edge from
    that aggregate into a downstream arrayed input.

    Regression: the previous rule made ``scalar: true`` unconditionally
    non-arrayed and rejected the ``colmap-triangulate.frame → stg-train.colmap_frames``
    edge with a 422, blocking the whole per-frame COLMAP → 4DGS pipeline.
    """
    graph = WorkflowGraph(
        nodes=[
            # Source: arrayable pack with fan-out on. Emits one T per shard;
            # framework aggregates N shards into arrayed<T> at the parent.
            GraphNode(
                id="a",
                algorithm_name="src",
                algorithm_version="0.1.0",
                assigned_node_id="node",
                arrayed_toggle=True,
            ),
            # Target: non-arrayable pack whose input is intrinsically arrayed
            # (the "consume the whole arrayed<T> in one invocation" shape
            # — e.g. stg-train's colmap_frames).
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
        ("src", "0.1.0"): _pk(
            {},
            {"o": {"tags": ["colmap"], "scalar": True}},
            arrayable=True,
        ),
        ("dst", "0.1.0"): _pk(
            {"i": {"tags": ["colmap"], "arrayed": True}},
            {"out": {"tags": ["x"]}},
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


def test_fanout_off_scalar_output_to_arrayed_input_still_rejected() -> None:
    """Same source pack as the fan-out test, but with ``arrayed_toggle=False``.

    Without the toggle the pack runs as a single invocation and its scalar
    output stays scalar — feeding it into an arrayed input must still fail
    (no implicit broadcast on scalar-output side).
    """
    graph = WorkflowGraph(
        nodes=[
            GraphNode(
                id="a",
                algorithm_name="src",
                algorithm_version="0.1.0",
                assigned_node_id="node",
                # Fan-out OFF — one invocation, one scalar output.
                arrayed_toggle=False,
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
        ("src", "0.1.0"): _pk(
            {},
            {"o": {"tags": ["colmap"], "scalar": True}},
            arrayable=True,
        ),
        ("dst", "0.1.0"): _pk(
            {"i": {"tags": ["colmap"], "arrayed": True}},
            {"out": {"tags": ["x"]}},
        ),
    }
    issues = validate_snapshot(
        graph,
        packs_by_key=packs,
        online_node_ids={"node"},
        packs_offered_by_node={"node": {("src", "0.1.0"), ("dst", "0.1.0")}},
    )
    edge_issues = [i for i in issues if i.where.startswith("edge:")]
    assert edge_issues, "expected validation issue when fan-out is off"
    assert any("scalar output" in i.message for i in edge_issues), edge_issues


def test_arrayed_output_to_scalar_input_allowed() -> None:
    """arrayed output → scalar: true input must be accepted (broadcast semantic)."""
    graph = WorkflowGraph(
        nodes=[
            GraphNode(
                id="a",
                algorithm_name="src",
                algorithm_version="0.1.0",
                assigned_node_id="node",
                arrayed_toggle=True,
            ),
            GraphNode(
                id="b",
                algorithm_name="dst",
                algorithm_version="0.1.0",
                assigned_node_id="node",
                arrayed_toggle=True,
            ),
        ],
        edges=[GraphEdge(id="e", source="a", sourceHandle="o", target="b", targetHandle="i")],
    )
    packs = {
        ("src", "0.1.0"): _pk({}, {"o": {"tags": ["colmap-cameras-txt"]}}, arrayable=True),
        ("dst", "0.1.0"): _pk(
            {"i": {"tags": ["colmap-cameras-txt"], "scalar": True}},
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


def test_scalar_output_to_scalar_input_allowed() -> None:
    """scalar: true output → scalar: true input: both non-arrayed, always OK."""
    graph = WorkflowGraph(
        nodes=[
            GraphNode(
                id="a",
                algorithm_name="src",
                algorithm_version="0.1.0",
                assigned_node_id="node",
                arrayed_toggle=True,
            ),
            GraphNode(
                id="b",
                algorithm_name="dst",
                algorithm_version="0.1.0",
                assigned_node_id="node",
                arrayed_toggle=True,
            ),
        ],
        edges=[GraphEdge(id="e", source="a", sourceHandle="o", target="b", targetHandle="i")],
    )
    packs = {
        ("src", "0.1.0"): _pk(
            {}, {"o": {"tags": ["colmap-cameras-txt"], "scalar": True}}, arrayable=True
        ),
        ("dst", "0.1.0"): _pk(
            {"i": {"tags": ["colmap-cameras-txt"], "scalar": True}},
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
            GraphNode(
                id="a", algorithm_name="src", algorithm_version="0.1.0", assigned_node_id="node"
            ),
            GraphNode(
                id="b", algorithm_name="dst", algorithm_version="0.1.0", assigned_node_id="node"
            ),
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
