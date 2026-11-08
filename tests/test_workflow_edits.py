"""File editor consistency: patches, disk changes, CAS and recovery."""

import pytest
from fastapi.testclient import TestClient

from hololab.gateway.app import create_app
from hololab.gateway.workflow_files import WorkflowDocument, read_document, write_document
from hololab.gateway.workflows import GraphNode, WorkflowGraph


@pytest.fixture
def editor(tmp_path, monkeypatch):
    monkeypatch.setenv("HOLOLAB_HOME", str(tmp_path / "user"))
    root = tmp_path / "files"
    root.mkdir()
    path = root / "one.workflow"
    graph = WorkflowGraph(
        nodes=[
            GraphNode(
                id="a", algorithm_name="p", algorithm_version="1.0.0", params={"x": 1, "y": 1}
            )
        ]
    )
    write_document(path, WorkflowDocument(id="one", name="one", graph=graph))
    with TestClient(create_app(db_path=tmp_path / "db.sqlite", workflows_dir=root)) as client:
        yield client, path


def edit(ws, ops, rev=0, request="test"):
    ws.send_json(
        {
            "kind": "workflow_edit",
            "payload": {"id": "one", "base_rev": rev, "ops": ops, "request_id": request},
        }
    )
    return ws.receive_json()["payload"]


def test_patch_immediately_changes_reads_then_durable_ack(editor):
    client, path = editor
    with client.websocket_connect("/ws/frontend") as ws:
        ack = edit(
            ws, [{"op": "node.map.set", "id": "a", "field": "params", "key": "x", "value": 2}]
        )
        assert ack["rev"] == 1 and not ack["saved"]
        assert client.get("/api/workflows/one").json()["graph"]["nodes"][0]["params"] == {
            "x": 2,
            "y": 1,
        }
        saved = ws.receive_json()["payload"]
        assert saved["saved"] and saved["rev"] == 1
        assert read_document(path).graph.nodes[0].params["x"] == 2


def test_external_edit_before_debounce_wins_without_clobber(editor, monkeypatch):
    client, path = editor
    with client.websocket_connect("/ws/frontend") as ws:
        edit(ws, [{"op": "name.set", "value": "local"}])
        document = read_document(path)
        document.name = "external"
        write_document(path, document)

        def no_recursive_scan(*args, **kwargs):
            raise AssertionError("watcher changes must not rescan the entire workspace")

        monkeypatch.setattr(type(path), "rglob", no_recursive_scan)
        client.portal.call(client.app.state.workflow_files.scan, [str(path)])
        changed = ws.receive_json()["payload"]
        assert changed["name"] == "external" and changed["rev"] == 2
        assert client.get("/api/workflows/one").json()["name"] == "external"
    assert read_document(path).name == "external"


def test_delete_broadcasts_missing_and_recreate_preserves_id(editor):
    client, path = editor
    with client.websocket_connect("/ws/frontend") as ws:
        path.unlink()
        client.post("/api/workflows/reload")
        assert ws.receive_json()["payload"]["missing"]
        result = client.post("/api/workflows/one/recreate")
        assert result.status_code == 200
        assert read_document(path).id == "one"
        assert not read_document(path).graph.nodes


def test_rejected_patch_is_only_sent_to_origin(editor):
    client, _ = editor
    with (
        client.websocket_connect("/ws/frontend") as first,
        client.websocket_connect("/ws/frontend") as other,
    ):
        result = edit(first, [{"op": "name.set", "value": "stale"}], rev=5)
        assert result["rejected"] and result["rev"] == 0
        edit(first, [{"op": "name.set", "value": "accepted"}])
        assert other.receive_json()["payload"]["name"] == "accepted"


@pytest.mark.parametrize(
    "op",
    [
        {"op": "node.set", "id": "a", "value": {"id": "new"}},
        {"op": "graph.set", "field": "nodes", "value": []},
        {"op": "node.map.set", "id": "a", "field": "id", "key": "x", "value": 2},
        {
            "op": "edge.add",
            "edge": {
                "id": "e",
                "source": "missing",
                "sourceHandle": "o",
                "target": "a",
                "targetHandle": "i",
            },
        },
    ],
)
def test_invalid_patch_is_atomic(editor, op):
    client, path = editor
    before = path.read_bytes()
    with client.websocket_connect("/ws/frontend") as ws:
        result = edit(ws, [{"op": "name.set", "value": "should rollback"}, op])
        assert result["rejected"] and result["error"]
        assert client.get("/api/workflows/one").json()["rev"] == 0
        assert path.read_bytes() == before


def test_open_mismatched_identity_requires_explicit_choice(editor):
    client, path = editor
    document = read_document(path)
    document.id = "different"
    write_document(path, document)
    response = client.post("/api/workflow-files/open", json={"path": str(path)})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "workflow_id_mismatch"
    response = client.post(
        "/api/workflow-files/open", json={"path": str(path), "replace_id": "one"}
    )
    assert response.status_code == 200 and response.json()["id"] == "different"


def test_programmatic_content_checks_revision_and_broadcasts(editor):
    client, path = editor
    with client.websocket_connect("/ws/frontend") as ws:
        response = client.put(
            "/api/workflows/one/content",
            json={
                "name": "api",
                "graph": {"nodes": [], "edges": []},
                "base_rev": 0,
                "expected_mtime": path.stat().st_mtime,
            },
        )
        assert response.status_code == 200 and response.json()["rev"] == 1
        assert ws.receive_json()["payload"]["name"] == "api"
        response = client.put(
            "/api/workflows/one/content",
            json={
                "name": "stale",
                "graph": {},
                "base_rev": 0,
                "expected_mtime": path.stat().st_mtime,
            },
        )
        assert response.status_code == 409


def test_restoring_identical_bytes_after_delete_advances_revision(editor):
    client, path = editor
    original = path.read_bytes()
    with client.websocket_connect("/ws/frontend") as ws:
        path.unlink()
        client.post("/api/workflows/reload")
        missing = ws.receive_json()["payload"]
        assert missing["missing"] and missing["rev"] == 1
        path.write_bytes(original)
        client.post("/api/workflows/reload")
        restored = ws.receive_json()["payload"]
        assert not restored["missing"] and restored["rev"] == 2


def test_invalid_external_json_cancels_pending_save_and_reports_path(editor):
    client, path = editor
    with client.websocket_connect("/ws/frontend") as ws:
        edit(ws, [{"op": "name.set", "value": "pending"}])
        path.write_text("{invalid json")
        client.post("/api/workflows/reload")
        error = ws.receive_json()["payload"]
        assert str(path) in error["error"]
        assert client.get("/api/workflows/one").status_code == 422
    assert path.read_text() == "{invalid json"
