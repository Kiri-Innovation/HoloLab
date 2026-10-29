from pathlib import Path

from hololab.gateway.port_type_ir import UnionType, compatible, from_legacy, render_type
from hololab.gateway.workflows import (
    GraphNode,
    InputPortView,
    OutputPortView,
    PackHandle,
    WorkflowGraph,
    resolved_port_types,
    tags_compatible,
    validate_snapshot,
)
from hololab.manifest.schema import load_manifest

VARIANTS = (
    (("colmap4d",), False, ()),
    (("colmap-folder",), True, ("frame",)),
)


def test_manifest_accepts_complete_input_union() -> None:
    manifest, _ = load_manifest(
        Path(__file__).parents[2]
        / "SpacetimeGaussians"
        / "packs"
        / "stg-init@0.3.2"
        / "manifest.yaml"
    )
    assert [variant.tags for variant in manifest.inputs["colmap_4d"].any_of] == [
        ["colmap4d"],
        ["colmap-folder"],
    ]


def test_compatible_accepts_either_union_variant_only() -> None:
    target = UnionType(
        variants=[
            from_legacy(["colmap4d"], False, []),
            from_legacy(["colmap-folder"], True, ["frame"]),
        ]
    )
    assert compatible(from_legacy(["colmap4d"], False, []), target, tags_compatible)
    assert compatible(
        from_legacy(["colmap-folder"], True, ["group"]),
        target,
        tags_compatible,
        require_axis_label_match=False,
    )
    assert not compatible(from_legacy(["colmap-folder"], False, []), target, tags_compatible)
    assert render_type(target) == "colmap4d | arrayed[frame]<colmap-folder>"


def test_resolved_input_preserves_union_and_fanout_is_rejected() -> None:
    port = InputPortView(tags=(), required=True, arrayed=False, any_of=VARIANTS)
    pack = PackHandle(
        inputs={"colmap_4d": port},
        outputs={"out": OutputPortView(tags=("x",), arrayed=False)},
        arrayable=True,
    )
    node = GraphNode(
        id="n",
        algorithm_name="stg-init",
        algorithm_version="0.3.1",
        assigned_node_id="worker",
        arrayed_toggle=True,
    )
    graph = WorkflowGraph(nodes=[node], edges=[])
    types, _ = resolved_port_types(
        node, pack, graph, {node.id: node}, {("stg-init", "0.3.1"): pack}
    )
    assert render_type(types.inputs["colmap_4d"].views.aggregate).startswith("colmap4d | ")
    issues = validate_snapshot(
        graph,
        packs_by_key={("stg-init", "0.3.1"): pack},
        online_node_ids={"worker"},
        packs_offered_by_node={"worker": {("stg-init", "0.3.1")}},
    )
    assert any("arrayed_toggle cannot be enabled" in issue.message for issue in issues)
