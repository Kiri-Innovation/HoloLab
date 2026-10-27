import json

from fastapi.testclient import TestClient

from hololab.gateway.app import create_app
from hololab.gateway.recipe_files import canonical, diagnostics, export_recipe, graph_from_document
from hololab.gateway.workflows import GraphNode, WorkflowGraph


def sample():
    return WorkflowGraph(
        variables={"dataset": {"type": "path", "default": ""}},
        variable_values={"dataset": "/secret/data"},
        nodes=[
            GraphNode(
                id="stg",
                algorithm_name="stg-train",
                algorithm_version="0.4.6",
                assigned_node_id="private-device",
                params={"iterations": 10000, "legacy_path": "/secret/inline"},
            )
        ],
    )


def test_export_excludes_local_state_and_roundtrips_missing_package():
    document = export_recipe(sample(), "STG", [])
    raw = canonical(document)
    for forbidden in (
        "/secret",
        "private-device",
        "position",
        "assigned_node_id",
        "variable_values",
        "preview_open",
        "code_identity",
    ):
        assert forbidden not in raw
    assert raw == canonical(document)
    loaded = graph_from_document(document)
    assert loaded.variable_values == {} and loaded.nodes[0].assigned_node_id is None
    assert loaded.nodes[0].local_params == ["legacy_path"]
    assert loaded.variables["dataset"].type == "path"
    assert document.diagnostics[0]["code"] == "missing_package"


def test_http_import_accepts_missing_dependencies_but_rejects_schema(tmp_path):
    with TestClient(create_app(db_path=tmp_path / "test.sqlite")) as client:
        document = export_recipe(sample(), "STG", []).model_dump()
        document["graph"]["nodes"][0]["algorithm_name"] = "definitely-absent"
        response = client.post("/api/recipes/import", json=document)
        assert response.status_code == 200, response.text
        wid = response.json()["workflow_id"]
        detail = client.get(f"/api/workflows/{wid}").json()
        assert detail["graph"]["nodes"][0]["assigned_node_id"] is None
        assert any(i["code"] == "missing_package" for i in detail["recipe_issues"])
        exported = client.get(f"/api/workflows/{wid}/recipe")
        assert exported.status_code == 200 and json.loads(exported.text)["schema_version"] == 1
        document["schema_version"] = 99
        assert client.post("/api/recipes/import", json=document).status_code == 422


def test_diagnostics_accepts_empty_default_but_reports_missing_required_parameter():
    graph = WorkflowGraph(
        nodes=[GraphNode(id="extract", algorithm_name="rig-extract", algorithm_version="0.1.0")]
    )
    catalog = [
        {
            "name": "rig-extract",
            "version": "0.1.0",
            "params": {
                "cams": {"type": "string", "default": ""},
                "source": {"type": "string"},
            },
        }
    ]

    issues = diagnostics(graph, catalog)

    assert {issue["message"] for issue in issues if issue["code"] == "missing_parameter"} == {
        "参数 source 未提供值"
    }
