from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from hololab.gateway.workflows import (
    GraphEdge,
    GraphNode,
    InputPortView,
    OutputPortView,
    PackHandle,
    WorkflowGraph,
    effective_output_tags,
    ports_compatible,
)
from hololab.manifest import load_manifest

REPO_ROOT = Path(__file__).parents[2]
PACK = REPO_ROOT / "hololab" / "packs" / "flatten-array@0.1.0"
sys.path.insert(0, str(REPO_ROOT / "packages" / "colmap4d" / "src"))
SPEC = importlib.util.spec_from_file_location("flatten_array_010", PACK / "flatten_array.py")
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_manifest_is_generic_two_to_one() -> None:
    manifest, _ = load_manifest(PACK / "manifest.yaml")
    assert manifest.inputs["arr"].tags == ["any"]
    assert manifest.inputs["arr"].dim_labels == ["outer", "inner"]
    assert manifest.inputs["arr"].require_axis_label_match is False
    assert manifest.outputs["flat"].tags == ["any"]
    assert manifest.outputs["flat"].tags_from == "arr"
    assert manifest.outputs["flat"].dim_labels == []


def test_opaque_file_and_directory_leaves_are_supported(tmp_path: Path) -> None:
    source = tmp_path / "source"
    file_leaf = source / "outer-a" / "file.bin"
    file_leaf.parent.mkdir(parents=True)
    file_leaf.write_bytes(b"opaque-file")
    directory_leaf = source / "outer-b" / "bundle"
    directory_leaf.mkdir(parents=True)
    (directory_leaf / "points3D.txt").write_text("opaque-directory")

    output = tmp_path / "flat"
    result = MODULE.flatten(source, output)
    assert (output / "outer-a__file.bin").read_bytes() == b"opaque-file"
    assert (output / "outer-b__bundle" / "points3D.txt").read_text() == "opaque-directory"
    assert (output / "outer-a__file.bin").is_symlink()
    assert (output / "outer-b__bundle").is_symlink()
    assert {row["leaf_kind"] for row in result["members"]} == {"file", "directory"}


def test_tags_from_resolves_image_and_connects_to_image_sink() -> None:
    source = GraphNode(id="source", algorithm_name="source", algorithm_version="1")
    flatten = GraphNode(id="flatten", algorithm_name="flatten-array", algorithm_version="0.1.0")
    graph = WorkflowGraph(
        nodes=[source, flatten],
        edges=[
            GraphEdge(
                id="edge", source="source", sourceHandle="out", target="flatten", targetHandle="arr"
            )
        ],
    )
    packs = {
        ("source", "1"): PackHandle(
            inputs={},
            outputs={
                "out": OutputPortView(tags=("image",), arrayed=True, dim_labels=("group", "cam"))
            },
        ),
        ("flatten-array", "0.1.0"): PackHandle(
            inputs={
                "arr": InputPortView(
                    tags=("any",),
                    required=True,
                    arrayed=True,
                    dim_labels=("outer", "inner"),
                    require_axis_label_match=False,
                )
            },
            outputs={"flat": OutputPortView(tags=("any",), arrayed=True, tags_from="arr")},
        ),
    }
    resolved = effective_output_tags(
        flatten,
        packs[("flatten-array", "0.1.0")],
        "flat",
        graph,
        {n.id: n for n in graph.nodes},
        packs,
    )
    assert resolved == ["image"]
    assert ports_compatible(resolved, True, ["image"], True, src_dim_labels=[], tgt_dim_labels=[])
