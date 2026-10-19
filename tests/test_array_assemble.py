import pytest
from pydantic import ValidationError

from hololab.gateway.array_assemble import (
    AssemblySlots,
    assembly_type,
    concrete_type_key,
    ordered_inputs,
)
from hololab.gateway.port_type_ir import Axis, ObjectType, UnknownType, wrap_array
from hololab.gateway.workflows import tags_compatible


def obj(*tags):
    return ObjectType(tags=list(tags))


def test_defaults_singleton_and_empty():
    c = AssemblySlots()
    assert len(c.slots) == 2
    assert assembly_type(c, {"slot-1": obj("image")}) == wrap_array(obj("image"), "item")
    assert ordered_inputs(c, {"slot-1": "handle"}) == [("0000", "handle")]
    with pytest.raises(ValueError, match="at least one"):
        assembly_type(c, {})


def test_strict_group_rule_does_not_change_ordinary_compatibility():
    assert tags_compatible(["video-source", "extra"], ["video-source"])
    with pytest.raises(ValueError, match="same type"):
        assembly_type(
            AssemblySlots(), {"slot-0": obj("video-source", "extra"), "slot-1": obj("video-source")}
        )
    with pytest.raises(ValueError, match="same type"):
        assembly_type(AssemblySlots(), {"slot-0": obj("video-source"), "slot-1": obj("image")})
    assert concrete_type_key(obj("a", "b")) == concrete_type_key(obj("b", "a"))


def test_order_ids_anchor_and_roundtrip():
    c = AssemblySlots(
        slots=["third", "first", "empty"], type_anchor_slot="first", axis_label="layer"
    )
    assert AssemblySlots.model_validate_json(c.model_dump_json()) == c
    assert ordered_inputs(c, {"first": "A", "third": "B"}) == [("0000", "B"), ("0001", "A")]
    assert assembly_type(c, {"third": obj("float")}) == wrap_array(obj("float"), "layer")


def test_nested_without_flattening_and_depth_limit():
    c = AssemblySlots()
    inner = wrap_array(obj("image"), "frame")
    assert assembly_type(c, {"slot-0": inner}) == wrap_array(inner, "item")
    with pytest.raises(ValueError, match="two collection axes"):
        assembly_type(c, {"slot-0": wrap_array(inner, "camera")})
    with pytest.raises(ValueError, match="same type"):
        assembly_type(c, {"slot-0": inner, "slot-1": wrap_array(obj("image"), "other")})


@pytest.mark.parametrize("t", [obj("any"), UnknownType(reason="unbound")])
def test_unresolved_types_rejected(t):
    with pytest.raises(ValueError):
        assembly_type(AssemblySlots(), {"slot-0": t})


@pytest.mark.parametrize(
    "args",
    [
        {"slots": ["x"]},
        {"slots": ["x", "x"]},
        {"slots": ["../x", "y"]},
        {"type_anchor_slot": "missing"},
        {"axis_label": " "},
    ],
)
def test_invalid_slot_contract(args):
    with pytest.raises(ValidationError):
        AssemblySlots(**args)


def test_content_axes_are_preserved():
    a = ObjectType(tags=["bundle"], contentAxes=[Axis(label="camera")])
    assert assembly_type(AssemblySlots(), {"slot-0": a}).element == a
    with pytest.raises(ValueError, match="same type"):
        assembly_type(AssemblySlots(), {"slot-0": a, "slot-1": obj("bundle")})


def graph_fixture(second_tag="video-source"):
    from hololab.gateway.workflows import (
        GraphEdge,
        GraphNode,
        InputPortView,
        OutputPortView,
        PackHandle,
        WorkflowGraph,
    )

    nodes = [
        GraphNode(id=i, algorithm_name=i, algorithm_version="1", assigned_node_id="worker")
        for i in ("a", "b", "sink")
    ]
    nodes.append(
        GraphNode(
            id="assembly",
            kind="intrinsic",
            operation="array-assemble",
            assembly=AssemblySlots(axis_label="layer"),
            assigned_node_id="worker",
        )
    )
    edges = [
        GraphEdge(id=i, source=i, sourceHandle="out", target="assembly", targetHandle=f"slot-{j}")
        for j, i in enumerate(("a", "b"))
    ]
    edges.append(
        GraphEdge(
            id="consume",
            source="assembly",
            sourceHandle="out",
            target="sink",
            targetHandle="videos",
        )
    )
    packs = {
        ("a", "1"): PackHandle(
            inputs={}, outputs={"out": OutputPortView(tags=("video-source",), arrayed=False)}
        ),
        ("b", "1"): PackHandle(
            inputs={}, outputs={"out": OutputPortView(tags=(second_tag,), arrayed=False)}
        ),
        ("sink", "1"): PackHandle(
            inputs={
                "videos": InputPortView(
                    tags=("video-source",), required=True, arrayed=True, dim_labels=("layer",)
                )
            },
            outputs={},
        ),
    }
    return WorkflowGraph(nodes=nodes, edges=edges), packs


def test_intrinsic_is_executable_and_preserves_consumer_contract():
    from hololab.gateway.workflows import (
        agent_graph_dict,
        execution_topological_order,
        graph_from_json,
        graph_to_json,
        validate_snapshot,
    )

    graph, packs = graph_fixture()
    assert graph_from_json(graph_to_json(graph)) == graph
    assert execution_topological_order(graph).index("assembly") < execution_topological_order(
        graph
    ).index("sink")
    assert (
        validate_snapshot(
            graph,
            packs_by_key=packs,
            online_node_ids={"worker"},
            packs_offered_by_node={"worker": set(packs)},
        )
        == []
    )
    resolved = agent_graph_dict(graph, packs_by_key=packs)
    node = next(n for n in resolved["nodes"] if n["id"] == "assembly")
    assert node["resolved_outputs"]["out"] == {
        "tags": ["video-source"],
        "arrayed": True,
        "dim_labels": ["layer"],
    }


def test_graph_rejects_mixed_inputs_and_duplicate_slot():
    from hololab.gateway.workflows import validate_snapshot

    graph, packs = graph_fixture("image")
    issues = validate_snapshot(
        graph,
        packs_by_key=packs,
        online_node_ids={"worker"},
        packs_offered_by_node={"worker": set(packs)},
    )
    assert any("same type" in i.message for i in issues)
    graph.edges[1].targetHandle = "slot-0"
    issues = validate_snapshot(
        graph,
        packs_by_key=packs,
        online_node_ids={"worker"},
        packs_offered_by_node={"worker": set(packs)},
    )
    assert any("multiple connections" in i.message for i in issues)


def test_materialization_keeps_file_directory_and_durable_remote_copy(tmp_path):
    from hololab.node.array_assemble import assemble

    local = tmp_path / "local"
    local.write_text("[0,0,1]")
    remote = tmp_path / "remote"
    remote.mkdir()
    (remote / "video.mp4").write_bytes(b"fixture")
    out = tmp_path / "out"
    assemble([("0000", str(local), True), ("0001", str(remote), False)], out)
    assert (out / "0000").is_symlink() and (out / "0000").is_file()
    assert (out / "0001").is_dir() and not (out / "0001").is_symlink()
    import shutil

    shutil.rmtree(remote)
    assert (out / "0001" / "video.mp4").read_bytes() == b"fixture"
    assert (out / ".hololab-done").exists()


def test_failure_does_not_publish_partial_array(tmp_path):
    from hololab.node.array_assemble import assemble

    source = tmp_path / "source"
    source.write_text("1")
    with pytest.raises(FileNotFoundError):
        assemble(
            [("0000", str(source), True), ("0001", str(tmp_path / "missing"), True)],
            tmp_path / "out",
        )
    assert not (tmp_path / "out").exists()
    assert not list(tmp_path.glob(".assemble-*"))


@pytest.mark.asyncio
async def test_remote_handle_location_uses_fileserver_relative_path():
    import asyncio
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from hololab.gateway.app import _dispatch_node_frame
    from hololab.protocol.messages import HandleLocateReq

    ws = SimpleNamespace(send_text=AsyncMock())
    consumer = SimpleNamespace(node_id="consumer", ws=ws, send_lock=asyncio.Lock(), protocol_v=1)
    producer = SimpleNamespace(
        advertised_url="http://worker:8829", workspace_root="/workspace", legacy_workspace_roots=[]
    )
    handle = SimpleNamespace(node_id="producer", path="/workspace/w/wf/j/job/out", storage="dir")
    handles = SimpleNamespace(get=AsyncMock(return_value=handle))
    registry = SimpleNamespace(get_session=lambda _: producer)
    await _dispatch_node_frame(
        "handle_locate_req",
        HandleLocateReq(handle_id="h"),
        consumer,
        None,
        handles,
        None,
        registry,
        None,
        None,
    )
    frame = json.loads(ws.send_text.call_args.args[0])
    assert frame["payload"]["http_url"] == "http://worker:8829/w/wf/j/job/out"
