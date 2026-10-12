"""Backend IR, compatibility and API projections: no rendered-string parsing."""

import itertools

import pytest
from pydantic import ValidationError

from hololab.gateway.models import WorkflowGraphOut
from hololab.gateway.port_type_ir import (
    ObjectType,
    PortTypes,
    TypeViews,
    UnknownType,
    compatible,
    from_legacy,
    render_type,
    views_from_aggregate,
)
from hololab.gateway.workflows import (
    GraphEdge,
    GraphNode,
    InputPortView,
    OutputPortView,
    PackHandle,
    WorkflowGraph,
    agent_graph_dict,
    ports_compatible,
    tags_compatible,
    validate_snapshot,
)


@pytest.mark.parametrize(
    "arrayed,labels,mode",
    itertools.product(
        [False, True],
        [[], ["frame"], ["frame", "cam"]],
        ["none", "map-input", "broadcast-input", "collect-output"],
    ),
)
def test_structural_views_round_trip(arrayed, labels, mode):
    aggregate = from_legacy(["image"], arrayed, labels)
    views = views_from_aggregate(aggregate, mode)
    assert TypeViews.model_validate_json(views.model_dump_json()) == views
    assert views.aggregate == aggregate  # never double-wrap an aggregate
    if mode in ("map-input", "collect-output"):
        assert views.invocation == (
            aggregate.element if arrayed else UnknownType(reason="not-array")
        )
    else:
        assert views.invocation == aggregate
    if arrayed:
        text = render_type(aggregate)
        assert text.count("<") == text.count(">") == max(1, len(labels))


@pytest.mark.parametrize(
    "src_arr,tgt_arr,scalar,a,b",
    itertools.product(
        [False, True],
        [False, True],
        [False, True],
        [[], [""], ["frame"], ["frame", "cam"]],
        [[], [""], ["frame"], ["cam"]],
    ),
)
def test_compatibility_matrix(src_arr, tgt_arr, scalar, a, b):
    expected = src_arr == tgt_arr or (src_arr and scalar)
    if src_arr and tgt_arr:
        expected &= (len(a) or 1) == (len(b) or 1)
        expected &= all(
            not s or not t or s == t for s, t in zip(a or [""], b or [""], strict=False)
        )
    assert (
        ports_compatible(["image"], src_arr, ["image_sequence"], tgt_arr, scalar, a, b) == expected
    )
    assert not ports_compatible(["image"], src_arr, ["mesh"], tgt_arr, scalar, a, b)


def graph_fixture():
    source = GraphNode(
        id="s", algorithm_name="source", algorithm_version="1", params={"dims": ["frame", "cam"]}
    )
    drop = GraphNode(id="d", algorithm_name="drop", algorithm_version="1")
    target = GraphNode(id="t", algorithm_name="target", algorithm_version="1")
    packs = {
        ("source", "1"): PackHandle(
            {}, {"out": OutputPortView(("image",), True, dim_labels_from="dims")}
        ),
        ("drop", "1"): PackHandle(
            {"in": InputPortView(("image",), True, True, dim_labels=("frame", "cam"))},
            {
                "out": OutputPortView(
                    ("any",),
                    False,
                    tags_from="in",
                    dim_labels_from_input="in",
                    dim_labels_drop_outer=1,
                )
            },
        ),
        ("target", "1"): PackHandle(
            {"in": InputPortView(("image",), True, True, dim_labels=("cam",))}, {}
        ),
    }
    graph = WorkflowGraph(
        nodes=[source, drop, target],
        edges=[
            GraphEdge(id="sd", source="s", sourceHandle="out", target="d", targetHandle="in"),
            GraphEdge(id="dt", source="d", sourceHandle="out", target="t", targetHandle="in"),
        ],
    )
    return graph, packs


def test_api_validation_share_derived_type_and_keep_legacy_projection():
    graph, packs = graph_fixture()
    payload = agent_graph_dict(graph, packs)
    node = next(n for n in payload["nodes"] if n["id"] == "d")
    assert node["resolved_outputs"] == {
        "out": {"tags": ["image"], "arrayed": True, "dim_labels": ["cam"]}
    }
    assert node["port_types"]["outputs"]["out"]["views"]["aggregate"] == {
        "kind": "array",
        "axis": {"label": "cam"},
        "element": {"kind": "object", "tags": ["image"], "contentAxes": []},
    }
    assert (
        node["port_types"]["outputs"]["out"]["runtime"]["axes"]["evidence"]["status"] == "unknown"
    )
    # FastAPI response models retain the additive field; GET -> save strips it.
    response = WorkflowGraphOut.model_validate(payload).model_dump()
    assert response["nodes"][1]["port_types"]["schemaVersion"] == 1
    restored = WorkflowGraph.model_validate(
        {"nodes": payload["nodes"], "edges": graph.model_dump()["edges"]}
    )
    assert restored.model_dump() == graph.model_dump()
    issues = validate_snapshot(
        graph, packs_by_key=packs, online_node_ids=set(), packs_offered_by_node={}
    )
    assert not [i for i in issues if i.where.startswith("edge:")]
    graph.nodes[0].params["dims"] = ["frame", "other"]
    issues = validate_snapshot(
        graph, packs_by_key=packs, online_node_ids=set(), packs_offered_by_node={}
    )
    error = next(i.message for i in issues if i.where == "edge:dt")
    assert "arrayed[other]<image>" in error and "arrayed[cam]<image>" in error


def test_unknown_and_version_rejection():
    assert not compatible(
        UnknownType(reason="unresolved"), ObjectType(tags=["any"]), tags_compatible
    )
    with pytest.raises(ValidationError):
        PortTypes.model_validate({"schemaVersion": 2, "inputs": {}, "outputs": {}})


def test_http_graph_and_resolve_share_additive_contract(tmp_path, monkeypatch):
    from dataclasses import asdict

    from fastapi.testclient import TestClient

    from hololab.gateway.app import create_app

    graph, packs = graph_fixture()
    catalog = [dict(name=k[0], version=k[1], **asdict(p)) for k, p in packs.items()]
    with TestClient(create_app(db_path=tmp_path / "ir.sqlite")) as client:
        monkeypatch.setattr(client.app.state.registry, "catalog_json", lambda: catalog)

        async def seed():
            await client.app.state.workflows.save_draft(
                workflow_id="11111111-1111-4111-8111-111111111111", name="IR", graph=graph
            )

        client.portal.call(seed)
        response = client.get("/api/workflows/11111111-1111-4111-8111-111111111111")
        assert response.status_code == 200, response.text
        node = next(n for n in response.json()["graph"]["nodes"] if n["id"] == "d")
        resolved = client.get(
            "/api/resolve",
            params={"ref": "hololab://graph-node/11111111-1111-4111-8111-111111111111/d"},
        )
        assert resolved.status_code == 200, resolved.text
        resource = resolved.json()["resource"]
        assert resource["current_draft"]["port_types"] == node["port_types"]
        assert resource["current_draft"]["params"] == {}
        assert node["resolved_outputs"]["out"] == {
            "tags": ["image"],
            "arrayed": True,
            "dim_labels": ["cam"],
        }
