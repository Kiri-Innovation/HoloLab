"""Contract tests for the zero-copy generic ``preview`` utility pack.

The two fixtures model the two outputs of rig-temporal-grouping: a scalar
``rig_timeline`` directory and an ``arrayed<arrayed<image>>[group, cam]``
directory.  The rig pack itself belongs to a separate algorithm checkout, so
these tests exercise its published output contract through the framework's
real type resolvers and the preview pack's rendered shell.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from hololab.gateway.execution import _arrayed_port_depth
from hololab.gateway.workflows import (
    GraphEdge,
    GraphNode,
    InputPortView,
    OutputPortView,
    PackHandle,
    WorkflowGraph,
    effective_output_dim_labels,
    effective_output_tags,
    effective_port_arrayed,
    validate_snapshot,
)
from hololab.manifest import RenderContext, load_manifest, render_manifest

PACK_ROOT = Path(__file__).resolve().parent.parent / "packs"


def _manifest():
    return load_manifest(PACK_ROOT / "preview@0.1.0" / "manifest.yaml")[0]


def _preview_handle() -> PackHandle:
    return PackHandle(
        inputs={
            "in": InputPortView(tags=("any",), required=True, arrayed=False, dim_labels=("", ""))
        },
        outputs={
            "out": OutputPortView(
                tags=("any",),
                arrayed=False,
                tags_from="in",
                dim_labels_from_input="in",
            )
        },
        arrayable=True,
    )


def _rig_handle(
    *, tags: tuple[str, ...], arrayed: bool, dim_labels: tuple[str, ...] = ()
) -> PackHandle:
    return PackHandle(
        inputs={},
        outputs={"artifact": OutputPortView(tags=tags, arrayed=arrayed, dim_labels=dim_labels)},
    )


def _graph(*, arrayed_toggle: bool) -> WorkflowGraph:
    return WorkflowGraph(
        nodes=[
            GraphNode(
                id="rig",
                algorithm_name="rig-temporal-grouping",
                algorithm_version="0.1.3",
                assigned_node_id="n",
            ),
            GraphNode(
                id="preview",
                algorithm_name="preview",
                algorithm_version="0.1.0",
                assigned_node_id="n",
                arrayed_toggle=arrayed_toggle,
            ),
        ],
        edges=[
            GraphEdge(
                id="rig-to-preview",
                source="rig",
                sourceHandle="artifact",
                target="preview",
                targetHandle="in",
            )
        ],
    )


def _issues(graph: WorkflowGraph, packs: dict[tuple[str, str], PackHandle]):
    return validate_snapshot(
        graph,
        packs_by_key=packs,
        online_node_ids={"n"},
        packs_offered_by_node={"n": set(packs)},
    )


def test_manifest_declares_generic_zero_copy_arrayable_passthrough() -> None:
    m = _manifest()
    assert m.arrayable is True
    assert m.inputs["in"].tags == ["any"]
    assert m.inputs["in"].arrayed is False
    assert m.inputs["in"].dim_labels == ["", ""]
    assert m.outputs["out"].tags_from == "in"
    assert m.outputs["out"].dim_labels_from_input == "in"
    assert m.outputs["out"].dim_labels_drop_outer == 0
    assert m.outputs["out"].arrayed is False
    assert m.runtime.gpu.required is False
    assert m.params["content_dims"].default == 1
    assert m.inputs["in"].content_dims_from == "content_dims"
    assert m.idempotency is not None
    assert m.idempotency.marker == "{{ outputs.out }}/.hololab-done"


def test_timeline_path_preserves_rig_timeline_tag_for_tag_driven_preview() -> None:
    packs = {
        ("rig-temporal-grouping", "0.1.3"): _rig_handle(tags=("rig_timeline",), arrayed=False),
        ("preview", "0.1.0"): _preview_handle(),
    }
    graph = _graph(arrayed_toggle=False)
    assert _issues(graph, packs) == []
    preview = graph.nodes[1]
    assert effective_output_tags(
        preview, packs[("preview", "0.1.0")], "out", graph, {n.id: n for n in graph.nodes}, packs
    ) == ["rig_timeline"]
    assert (
        effective_output_dim_labels(
            preview,
            packs[("preview", "0.1.0")],
            "out",
            graph,
            {n.id: n for n in graph.nodes},
            packs,
        )
        == []
    )
    assert effective_port_arrayed(False, True, False) is False


def test_groups_path_preserves_nested_image_type_and_dims() -> None:
    packs = {
        ("rig-temporal-grouping", "0.1.3"): _rig_handle(
            tags=("image",), arrayed=True, dim_labels=("group", "cam")
        ),
        ("preview", "0.1.0"): _preview_handle(),
    }
    graph = _graph(arrayed_toggle=True)
    assert _issues(graph, packs) == []
    preview = graph.nodes[1]
    assert effective_output_tags(
        preview, packs[("preview", "0.1.0")], "out", graph, {n.id: n for n in graph.nodes}, packs
    ) == ["image"]
    assert effective_output_dim_labels(
        preview, packs[("preview", "0.1.0")], "out", graph, {n.id: n for n in graph.nodes}, packs
    ) == ["group", "cam"]
    assert effective_port_arrayed(False, True, True) is True


def test_content_dims_excludes_image_file_axis_from_fanout() -> None:
    spec = {"dim_labels": ["", ""], "content_dims_from": "content_dims"}
    assert _arrayed_port_depth("in", spec, {"content_dims": 1}) == 1
    assert _arrayed_port_depth("in", spec, {"content_dims": 0}) == 2


def test_rendered_exec_links_entries_and_marker_does_not_pollute_source(tmp_path: Path) -> None:
    source = tmp_path / "rig-output"
    source.mkdir()
    (source / "timeline.png").write_bytes(b"png")
    output = tmp_path / "job" / "out"
    output.mkdir(parents=True)  # matches runtime's pre-created output directory
    rendered = render_manifest(
        _manifest(),
        RenderContext(inputs={"in": str(source)}, outputs={"out": str(output)}),
    )
    subprocess.run(["bash", "-c", rendered.shell], check=True)
    assert output.is_dir()
    assert (output / "timeline.png").is_symlink()
    assert (output / "timeline.png").resolve() == source / "timeline.png"
    assert (output / "timeline.png").read_bytes() == b"png"
    assert not (source / ".hololab-done").exists()
    assert (output / ".hololab-done").is_file()
    assert rendered.idempotency_marker == str(output / ".hololab-done")
