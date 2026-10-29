"""All shipped packs load; invocation axes remain separate from fan-out axes."""

from pathlib import Path

import pytest

from hololab.gateway.tag_probes import content_dim_count_for
from hololab.gateway.workflows import (
    GraphEdge,
    GraphNode,
    InputPortView,
    OutputPortView,
    PackHandle,
    WorkflowGraph,
    resolved_port_types,
)
from hololab.manifest import load_manifest

PACKS = Path(__file__).resolve().parents[1] / "community_packs"


@pytest.mark.parametrize(
    "manifest_path", sorted(PACKS.glob("*/manifest.yaml")), ids=lambda p: p.parent.name
)
def test_all_shipped_packs_load(manifest_path):
    manifest, _ = load_manifest(manifest_path)
    for spec in [*manifest.inputs.values(), *manifest.outputs.values()]:
        assert not {"image_sequence", "frame_sequence"}.intersection(spec.tags)
        if "image" in spec.tags:
            assert spec.storage.value == ("dir" if spec.arrayed else "file")
            assert content_dim_count_for(spec.tags) == 0


def test_undistort_invocation_and_parent_axes_are_separate():
    m, _ = load_manifest(PACKS / "image-undistort@0.5.2" / "manifest.yaml")
    assert m.outputs["images"].arrayed and m.outputs["images"].dim_labels == ["cam"]
    assert not m.outputs["cams"].arrayed and m.outputs["cams"].dim_labels == []
    pack = PackHandle(
        {
            k: InputPortView(
                tuple(v.tags),
                v.required,
                v.arrayed,
                scalar=v.scalar,
                dim_labels=tuple(v.dim_labels),
            )
            for k, v in m.inputs.items()
        },
        {
            k: OutputPortView(
                tuple(v.tags), v.arrayed, scalar=v.scalar, dim_labels=tuple(v.dim_labels)
            )
            for k, v in m.outputs.items()
        },
        arrayable=True,
    )
    source = GraphNode(id="s", algorithm_name="source", algorithm_version="1")
    node = GraphNode(
        id="und", algorithm_name=m.name, algorithm_version=m.version, arrayed_toggle=True
    )
    graph = WorkflowGraph(
        nodes=[source, node],
        edges=[
            GraphEdge(id="e", source="s", sourceHandle="out", target="und", targetHandle="images")
        ],
    )
    packs = {
        ("source", "1"): PackHandle(
            {}, {"out": OutputPortView(("image",), True, dim_labels=("frame", "cam"))}
        ),
        (m.name, m.version): pack,
    }
    types, resolved = resolved_port_types(node, pack, graph, {n.id: n for n in graph.nodes}, packs)
    assert resolved["images"]["dim_labels"] == ["frame", "cam"]
    assert resolved["cams"]["dim_labels"] == ["frame"]
    assert types.outputs["images"].views.invocation.axis.label == "cam"
    assert types.outputs["cams"].views.invocation.kind == "object"
