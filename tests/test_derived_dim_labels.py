"""``dim_labels_from_input`` + ``dim_labels_drop_outer`` derivation.

Element-access packs (``get-index``) collapse the outermost dim of their
wired arrayed input. Rather than have every pack author re-declare that
shape, the manifest names an input port and the drop count; the framework
resolves the runtime dim_labels by walking the wire back to the producer
and dropping the outer N layers.

Covers:

* Basic derivation: 2-D upstream → 1-D output (drop=1).
* Chained derivation: drop cascades through multiple hops.
* No wire yet → returns ``None`` (chip degrades to scalar; no fake shape).
* Drop >= depth → returns ``[]`` (fully collapsed to scalar).
* Static ``dim_labels_from`` (params key) is honoured when the source is
  itself declared derivation-free.
"""

from __future__ import annotations

from hololab.gateway.workflows import (
    GraphEdge,
    GraphNode,
    InputPortView,
    OutputPortView,
    PackHandle,
    WorkflowGraph,
    effective_output_dim_labels,
)


def _pk_regroup() -> PackHandle:
    return PackHandle(
        inputs={"in": InputPortView(tags=("any",), required=True, arrayed=True)},
        outputs={
            "out": OutputPortView(
                tags=("any",),
                arrayed=True,
                tags_from="in",
                dim_labels_from="output_dims",
            ),
        },
    )


def _pk_get_index() -> PackHandle:
    return PackHandle(
        inputs={"arr": InputPortView(tags=("any",), required=True, arrayed=True)},
        outputs={
            "item": OutputPortView(
                tags=("any",),
                arrayed=False,
                tags_from="arr",
                dim_labels_from_input="arr",
                dim_labels_drop_outer=1,
            ),
        },
    )


def _pk_producer_2d() -> PackHandle:
    return PackHandle(
        inputs={},
        outputs={
            "o": OutputPortView(
                tags=("image",),
                arrayed=True,
                dim_labels=("frame", "cam"),
            )
        },
    )


def _pk_producer_1d() -> PackHandle:
    return PackHandle(
        inputs={},
        outputs={
            "o": OutputPortView(
                tags=("image",),
                arrayed=True,
                dim_labels=("cam",),
            )
        },
    )


def _graph_two_hop(regroup_dims: list[str]) -> tuple[WorkflowGraph, dict, dict]:
    """producer2d -> regroup -> get-index -> ."""
    nodes = [
        GraphNode(id="src", algorithm_name="src2d", algorithm_version="0.1.0"),
        GraphNode(
            id="rg",
            algorithm_name="regroup",
            algorithm_version="0.2.0",
            params={"output_dims": regroup_dims},
        ),
        GraphNode(id="gi", algorithm_name="get-index", algorithm_version="0.1.0"),
    ]
    graph = WorkflowGraph(
        nodes=nodes,
        edges=[
            GraphEdge(id="e1", source="src", sourceHandle="o", target="rg", targetHandle="in"),
            GraphEdge(id="e2", source="rg", sourceHandle="out", target="gi", targetHandle="arr"),
        ],
    )
    packs = {
        ("src2d", "0.1.0"): _pk_producer_2d(),
        ("regroup", "0.2.0"): _pk_regroup(),
        ("get-index", "0.1.0"): _pk_get_index(),
    }
    node_by_id = {n.id: n for n in nodes}
    return graph, node_by_id, packs


def test_derives_from_upstream_and_drops_outer_layer() -> None:
    graph, node_by_id, packs = _graph_two_hop(["cam", "frame"])
    # regroup.out with output_dims=[cam,frame] → dim_labels=[cam,frame].
    # get-index.item derives from it dropping 1 → [frame].
    got = effective_output_dim_labels(
        node_by_id["gi"], packs[("get-index", "0.1.0")], "item", graph, node_by_id, packs
    )
    assert got == ["frame"]


def test_derivation_when_upstream_is_static_labels() -> None:
    node_src = GraphNode(id="src", algorithm_name="src1d", algorithm_version="0.1.0")
    node_gi = GraphNode(id="gi", algorithm_name="get-index", algorithm_version="0.1.0")
    graph = WorkflowGraph(
        nodes=[node_src, node_gi],
        edges=[GraphEdge(id="e", source="src", sourceHandle="o", target="gi", targetHandle="arr")],
    )
    packs = {
        ("src1d", "0.1.0"): _pk_producer_1d(),
        ("get-index", "0.1.0"): _pk_get_index(),
    }
    node_by_id = {"src": node_src, "gi": node_gi}
    # 1-D upstream, drop=1 → fully collapsed to scalar (empty label list).
    got = effective_output_dim_labels(
        node_gi, packs[("get-index", "0.1.0")], "item", graph, node_by_id, packs
    )
    assert got == []


def test_no_wire_returns_none() -> None:
    """No wire on the referenced input → return None so the chip shows nothing.

    Preferable to fabricating a shape from the pack's declared ``dim_labels``
    (empty), which would render as scalar and mislead the reader.
    """
    node_gi = GraphNode(id="gi", algorithm_name="get-index", algorithm_version="0.1.0")
    graph = WorkflowGraph(nodes=[node_gi], edges=[])
    packs = {("get-index", "0.1.0"): _pk_get_index()}
    got = effective_output_dim_labels(
        node_gi, packs[("get-index", "0.1.0")], "item", graph, {"gi": node_gi}, packs
    )
    assert got is None


def test_dim_labels_from_params_takes_precedence_when_only_declared() -> None:
    """A port declaring only ``dim_labels_from`` uses the node's params
    verbatim — same as before this feature landed."""

    node = GraphNode(
        id="rg",
        algorithm_name="regroup",
        algorithm_version="0.2.0",
        params={"output_dims": ["frame", "cam"]},
    )
    graph = WorkflowGraph(nodes=[node], edges=[])
    packs = {("regroup", "0.2.0"): _pk_regroup()}
    got = effective_output_dim_labels(
        node, packs[("regroup", "0.2.0")], "out", graph, {"rg": node}, packs
    )
    assert got == ["frame", "cam"]
