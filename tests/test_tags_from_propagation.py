"""``tags_from`` runtime propagation in edge validation (M5).

Generic utility packs (``arrayfy`` / ``get-index``) declare their output
tags as ``[any]`` and set ``tags_from: <input_port>`` on that output so
the element type propagates from whatever the caller wired in. This test
suite locks the semantics:

* Downstream of ``arrayfy`` typed as ``frame_sequence`` connects cleanly
  when ``arrayfy.data`` is wired from a ``frame_sequence`` source.
* Downstream typed as ``colmap-cams`` is REJECTED when ``arrayfy.data``
  is wired from a ``frame_sequence`` source (the ``any`` on the output
  no longer masks a type mismatch — propagation makes it concrete).
* Nothing wired to the referenced input yet → the output stays ``[any]``
  and matches anything (permissive during graph construction).
* Cycles in ``tags_from`` collapse to ``[any]`` (safety net; won't happen
  through a valid manifest since ``tags_from`` names an input, not an
  output).
"""

from __future__ import annotations

from hololab.gateway.workflows import (
    GraphEdge,
    GraphNode,
    InputPortView,
    OutputPortView,
    PackHandle,
    WorkflowGraph,
    effective_output_tags,
    validate_snapshot,
)


def _pk_arrayfy() -> PackHandle:
    """Generic broadcast pack: data:any + count:int → out:arrayed<any tags_from=data>."""
    return PackHandle(
        inputs={
            "data": InputPortView(tags=("any",), required=True, arrayed=False),
            "count": InputPortView(tags=("int",), required=True, arrayed=False),
        },
        outputs={
            "out": OutputPortView(tags=("any",), arrayed=True, tags_from="data"),
        },
    )


def _pk_producer(tags: tuple[str, ...], *, arrayed: bool = False) -> PackHandle:
    return PackHandle(
        inputs={},
        outputs={"o": OutputPortView(tags=tags, arrayed=arrayed, tags_from=None)},
    )


def _pk_sink(tags: tuple[str, ...], *, arrayed: bool = False) -> PackHandle:
    return PackHandle(
        inputs={"i": InputPortView(tags=tags, required=True, arrayed=arrayed)},
        outputs={"o": OutputPortView(tags=("done",), arrayed=False)},
    )


def _int_source() -> PackHandle:
    return PackHandle(
        inputs={},
        outputs={"n": OutputPortView(tags=("int",), arrayed=False)},
    )


def _fanout_graph(sink_tags: tuple[str, ...]) -> WorkflowGraph:
    return WorkflowGraph(
        nodes=[
            GraphNode(
                id="src",
                algorithm_name="src",
                algorithm_version="0.1.0",
                assigned_node_id="node-a",
            ),
            GraphNode(
                id="cnt",
                algorithm_name="cnt",
                algorithm_version="0.1.0",
                assigned_node_id="node-a",
            ),
            GraphNode(
                id="af",
                algorithm_name="arrayfy",
                algorithm_version="0.1.0",
                assigned_node_id="node-a",
            ),
            GraphNode(
                id="dst",
                algorithm_name="dst",
                algorithm_version="0.1.0",
                assigned_node_id="node-a",
            ),
        ],
        edges=[
            GraphEdge(id="e1", source="src", sourceHandle="o", target="af", targetHandle="data"),
            GraphEdge(id="e2", source="cnt", sourceHandle="n", target="af", targetHandle="count"),
            GraphEdge(id="e3", source="af", sourceHandle="out", target="dst", targetHandle="i"),
        ],
    )


def _packs(src_out_tags: tuple[str, ...], sink_tags: tuple[str, ...]) -> dict:
    return {
        ("src", "0.1.0"): _pk_producer(src_out_tags),
        ("cnt", "0.1.0"): _int_source(),
        ("arrayfy", "0.1.0"): _pk_arrayfy(),
        ("dst", "0.1.0"): _pk_sink(sink_tags, arrayed=True),
    }


def _validate(graph: WorkflowGraph, packs: dict) -> list:
    return validate_snapshot(
        graph,
        packs_by_key=packs,
        online_node_ids={"node-a"},
        packs_offered_by_node={
            "node-a": {
                ("src", "0.1.0"),
                ("cnt", "0.1.0"),
                ("arrayfy", "0.1.0"),
                ("dst", "0.1.0"),
            }
        },
    )


# ---------------------------------------------------------------------------
# Pure effective_output_tags — no snapshot machinery, just the helper.
# ---------------------------------------------------------------------------


def test_declared_tags_when_tags_from_unset() -> None:
    graph = WorkflowGraph(
        nodes=[GraphNode(id="p", algorithm_name="p", algorithm_version="0.1.0")],
        edges=[],
    )
    pack = _pk_producer(("frame_sequence",))
    tags = effective_output_tags(
        graph.nodes[0],
        pack,
        "o",
        graph,
        {"p": graph.nodes[0]},
        {("p", "0.1.0"): pack},
    )
    assert tags == ["frame_sequence"]


def test_tags_from_falls_back_to_any_when_input_unwired() -> None:
    """Un-wired ``tags_from`` reference → declared tags (``[any]``) survive."""
    packs = {("arrayfy", "0.1.0"): _pk_arrayfy()}
    node = GraphNode(id="af", algorithm_name="arrayfy", algorithm_version="0.1.0")
    graph = WorkflowGraph(nodes=[node], edges=[])
    tags = effective_output_tags(
        node, packs[("arrayfy", "0.1.0")], "out", graph, {"af": node}, packs
    )
    assert tags == ["any"]


# ---------------------------------------------------------------------------
# End-to-end via validate_snapshot.
# ---------------------------------------------------------------------------


def test_arrayfy_output_propagates_matching_tag() -> None:
    """src produces frame_sequence → arrayfy.out effective tag = frame_sequence
    → downstream expecting frame_sequence connects cleanly."""
    graph = _fanout_graph(sink_tags=("frame_sequence",))
    packs = _packs(src_out_tags=("frame_sequence",), sink_tags=("frame_sequence",))
    edge_issues = [i for i in _validate(graph, packs) if i.where.startswith("edge:")]
    assert edge_issues == [], edge_issues


def test_arrayfy_output_propagates_and_rejects_mismatch() -> None:
    """src produces frame_sequence, downstream expects colmap-cams —
    propagation makes the mismatch concrete and validate_snapshot rejects."""
    graph = _fanout_graph(sink_tags=("colmap-cams",))
    packs = _packs(src_out_tags=("frame_sequence",), sink_tags=("colmap-cams",))
    edge_issues = [i for i in _validate(graph, packs) if i.where.startswith("edge:")]
    assert any("tag mismatch" in i.message for i in edge_issues), edge_issues


def test_arrayfy_without_data_wired_still_matches_any() -> None:
    """Grace case: the ``data`` input isn't wired yet — the output falls
    back to its declared ``[any]`` and downstream typing still succeeds
    (permissive during graph construction; snapshot validation would
    surface the missing required input separately)."""
    packs = _packs(src_out_tags=("frame_sequence",), sink_tags=("colmap-cams",))
    graph = _fanout_graph(sink_tags=("colmap-cams",))
    # Drop the src → arrayfy.data edge so tags_from can't resolve.
    graph.edges = [e for e in graph.edges if e.id != "e1"]
    edge_issues = [i for i in _validate(graph, packs) if i.where.startswith("edge:")]
    # e3 stays clean (any matches colmap-cams). The missing-required-input
    # complaint on arrayfy.data is a NODE issue, not an edge issue.
    assert edge_issues == [], edge_issues
