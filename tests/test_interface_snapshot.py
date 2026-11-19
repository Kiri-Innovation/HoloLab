"""Declaration evidence stays portable and never serializes runtime observations."""

import hashlib
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from hololab.gateway.workflow_files import (
    WorkflowDocument,
    read_document,
    serialize,
    write_document,
)
from hololab.gateway.workflows import (
    GraphNode,
    InputPortView,
    PackHandle,
    WorkflowGraph,
    agent_graph_dict,
)


def evidence():
    contract = dict(
        name="k4d/init",
        version="0.1.7",
        manifest_hash="a" * 64,
        arrayable=False,
        inputs={
            "in": {
                "any_of": [
                    {"tags": ["colmap4d"], "arrayed": False},
                    {"tags": ["colmap-folder"], "arrayed": True, "dim_labels": ["frame"]},
                ]
            }
        },
        outputs={},
        source={"kind": "catalog"},
        params={},
    )
    raw = json.dumps(contract, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return dict(
        schemaVersion=1,
        declaration_json=raw,
        sha256=hashlib.sha256(raw.encode()).hexdigest(),
        future=None,
    )


def node(**extra):
    return GraphNode(id="n", algorithm_name="k4d/init", algorithm_version="0.1.7", **extra)


def test_portable_snapshot_and_extensions(tmp_path):
    n = node(interface_snapshot=evidence(), future={"x": None})
    graph = WorkflowGraph(nodes=[n])
    path = tmp_path / "one.workflow"
    write_document(path, WorkflowDocument(id="w", name="x", graph=graph))
    before = path.read_bytes()
    restored = read_document(path)
    assert restored.graph.nodes[0].interface_snapshot == evidence()
    assert restored.graph.nodes[0].model_extra == {"future": {"x": None}}
    assert serialize(restored) == before
    assert b'"port_types"' not in before and b'"runtime"' not in before
    assert node().interface_snapshot is None
    broken = evidence()
    broken["declaration_json"] += " "
    with pytest.raises(ValidationError, match="checksum"):
        node(interface_snapshot=broken)
    with pytest.raises(ValidationError, match="identity"):
        GraphNode(
            id="n", algorithm_name="other", algorithm_version="0.1.7", interface_snapshot=evidence()
        )


def test_union_declaration_is_not_lost_in_api_projection():
    n = node()
    pack = PackHandle(
        inputs={
            "in": InputPortView(
                tags=(),
                required=True,
                arrayed=False,
                any_of=((("colmap4d",), False, ()), (("colmap-folder",), True, ("frame",))),
            )
        },
        outputs={},
    )
    view = agent_graph_dict(WorkflowGraph(nodes=[n]), {("k4d/init", "0.1.7"): pack})
    declaration = view["nodes"][0]["port_types"]["inputs"]["in"]["declaration"]
    assert declaration["any_of"][1] == dict(
        tags=["colmap-folder"], arrayed=True, dim_labels=["frame"]
    )


def test_real_http_save_read_and_cross_file_roundtrip(tmp_path, monkeypatch):
    from hololab.gateway.app import create_app

    monkeypatch.setenv("HOLOLAB_HOME", str(tmp_path / "user"))
    root = tmp_path / "documents"
    root.mkdir()
    path = root / "one.workflow"
    write_document(path, WorkflowDocument(id="w", name="x", graph=WorkflowGraph(nodes=[node()])))
    with TestClient(create_app(db_path=tmp_path / "isolated.sqlite", workflows_dir=root)) as client:
        detail = client.get("/api/workflows/w").json()
        graph = detail["graph"]
        graph["nodes"][0]["interface_snapshot"] = evidence()
        response = client.put(
            "/api/workflows/w/content",
            json=dict(
                name="x", graph=graph, base_rev=detail["rev"], expected_mtime=detail["mtime"]
            ),
        )
        assert response.status_code == 200, response.text
        got = client.get("/api/workflows/w").json()["graph"]["nodes"][0]
        assert got["interface_snapshot"] == evidence()
        second = tmp_path / "cross-machine.workflow"
        second.write_bytes(serialize(read_document(path)))
        assert read_document(second).graph.nodes[0].interface_snapshot == evidence()


def test_recipe_export_import_preserves_envelope():
    from hololab.gateway.recipe_files import export_recipe, graph_from_document

    graph = WorkflowGraph(nodes=[node(interface_snapshot=evidence())])
    document = export_recipe(graph, "portable", [])
    restored = graph_from_document(document)
    assert restored.nodes[0].interface_snapshot == evidence()
