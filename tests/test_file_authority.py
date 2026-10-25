import json

import pytest
from fastapi.testclient import TestClient

from hololab.gateway.app import create_app
from hololab.gateway.recipe_files import export_recipe
from hololab.gateway.workflows import GraphNode, WorkflowGraph
from tests.test_frozen_execution import setup


def create(client):
    graph = WorkflowGraph(
        nodes=[
            GraphNode(
                id="a", algorithm_name="missing", algorithm_version="1.0.0", params={"count": 1}
            )
        ]
    )
    r = client.post("/api/workflows", json={"name": "file test", "graph": graph.model_dump()})
    assert r.status_code == 200, r.text
    wid = r.json()["workflow_id"]
    return wid, client.get(f"/api/workflows/{wid}").json()


def test_external_reload_api_save_and_conflict(tmp_path):
    with TestClient(create_app(db_path=tmp_path / "test.sqlite")) as client:
        wid, base = create(client)
        path = tmp_path / "workflows" / wid / "recipe.json"
        document = json.loads(path.read_text())
        document["graph"]["nodes"][0]["params"]["count"] = 2
        path.write_text(json.dumps(document))
        # Even overwrite cannot defeat the authoritative file CAS.
        response = client.post(
            "/api/workflows",
            json={**base, "base_updated_ts": base["updated_ts"], "overwrite": True},
        )
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["file_changes"]["nodes"] == ["a"]
        fresh = client.post(f"/api/workflows/{wid}/reload").json()
        assert fresh["graph"]["nodes"][0]["params"]["count"] == 2
        fresh["graph"]["nodes"][0]["params"]["count"] = 3
        response = client.post(
            "/api/workflows", json={**fresh, "base_updated_ts": fresh["updated_ts"]}
        )
        assert response.status_code == 200, response.text
        assert json.loads(path.read_text())["graph"]["nodes"][0]["params"]["count"] == 3
        path.unlink()
        response = client.get(f"/api/workflows/{wid}")
        assert response.status_code == 422 and "缺失" in response.text


def test_legacy_migration_keeps_local_bindings_out_of_file(tmp_path):
    with TestClient(create_app(db_path=tmp_path / "test.sqlite")) as client:
        store = client.app.state.workflows
        graph = WorkflowGraph(
            variables={"data": {"type": "path", "default": ""}},
            variable_values={"data": "/private/data"},
            nodes=[
                GraphNode(
                    id="a",
                    algorithm_name="missing",
                    algorithm_version="1.0.0",
                    assigned_node_id="device",
                    params={"path": "/private/source"},
                )
            ],
        )

        async def old():
            return await store._save_cached(workflow_id=None, name="legacy", graph=graph)

        row = client.portal.call(old)
        path = tmp_path / "workflows" / row.workflow_id / "recipe.json"
        assert not path.exists()
        detail = client.get(f"/api/workflows/{row.workflow_id}").json()
        assert "/private/" not in path.read_text() and "device" not in path.read_text()
        assert detail["graph"]["nodes"][0]["params"]["path"] == "/private/source"
        assert detail["graph"]["variable_values"] == {"data": "/private/data"}
        assert detail["graph"]["nodes"][0]["assigned_node_id"] == "device"


def test_file_commit_recovers_failed_db_cache(tmp_path, monkeypatch):
    with TestClient(create_app(db_path=tmp_path / "test.sqlite")) as client:
        wid, base = create(client)
        store = client.app.state.workflows
        original = store._save_cached

        async def fail(**kwargs):
            raise RuntimeError("simulated DB cache failure")

        monkeypatch.setattr(store, "_save_cached", fail)
        base["graph"]["nodes"][0]["params"]["count"] = 4
        with pytest.raises(RuntimeError, match="simulated"):
            client.post("/api/workflows", json={**base, "base_updated_ts": base["updated_ts"]})
        monkeypatch.setattr(store, "_save_cached", original)
        detail = client.get(f"/api/workflows/{wid}").json()
        assert detail["graph"]["nodes"][0]["params"]["count"] == 4


def test_import_preserves_dependency_references_offline_and_runs_after_binding(tmp_path):
    with TestClient(create_app(db_path=tmp_path / "test.sqlite")) as client:
        app, catalog, graph, received = setup(client, tmp_path)
        document = export_recipe(graph, "portable", catalog)
        document.dependencies[0]["hash"] = "a" * 64
        document.dependencies[0]["root"] = "../.."
        imported = client.post("/api/recipes/import", json=document.model_dump())
        assert imported.status_code == 200, imported.text
        wid = imported.json()["workflow_id"]
        exported = client.get(f"/api/workflows/{wid}/recipe").json()
        assert exported["dependencies"][0]["hash"] == "a" * 64
        assert exported["dependencies"][0]["root"] == "../.."
        # A separate unpinned legacy recipe exercises runnable local bindings.
        document.dependencies[0]["hash"] = None
        wid = client.post("/api/recipes/import", json=document.model_dump()).json()["workflow_id"]
        detail = client.get(f"/api/workflows/{wid}").json()
        assert detail["graph"]["nodes"][0]["assigned_node_id"] is None
        detail["graph"]["nodes"][0]["assigned_node_id"] = "node"
        response = client.post(
            "/api/workflows", json={**detail, "base_updated_ts": detail["updated_ts"]}
        )
        assert response.status_code == 200, response.text

        async def execute():
            from hololab.gateway.execution import run_snapshot
            from hololab.gateway.preparation import capture_code_identities, prepare_execution

            row = await app.state.workflows.get_draft(wid)
            frozen = await prepare_execution(app, row.graph)
            await capture_code_identities(app, frozen)
            snap = await app.state.workflows.create_snapshot(workflow_id=wid, graph=frozen)
            await run_snapshot(app, workflow_id=wid, snapshot_id=snap.snapshot_id, graph=frozen)

        client.portal.call(execute)
        assert received and received[0][0] == received[0][1] == {"count": 12}
        assert received[0][2] == "echo 12"


def test_external_file_changes_do_not_rewrite_frozen_snapshot(tmp_path):
    with TestClient(create_app(db_path=tmp_path / "test.sqlite")) as client:
        wid, _ = create(client)
        store = client.app.state.workflows

        async def freeze():
            row = await store.get_draft(wid)
            return await store.create_snapshot(workflow_id=wid, graph=row.graph)

        snapshot = client.portal.call(freeze)
        path = tmp_path / "workflows" / wid / "recipe.json"
        document = json.loads(path.read_text())
        document["graph"]["nodes"][0]["params"]["count"] = 100
        path.write_text(json.dumps(document))
        assert client.post(f"/api/workflows/{wid}/reload").status_code == 200

        async def read():
            return await store.get_snapshot(snapshot.snapshot_id)

        assert client.portal.call(read).graph.nodes[0].params["count"] == 1

        async def remove():
            await store.delete_draft(wid)

        client.portal.call(remove)
        assert not path.exists()
        assert list(path.parent.glob("recipe.deleted-*.json"))
        assert client.get(f"/api/workflows/{wid}").status_code == 404


def test_invalid_file_does_not_fall_back_to_cached_recipe(tmp_path):
    with TestClient(create_app(db_path=tmp_path / "test.sqlite")) as client:
        wid, _ = create(client)
        path = tmp_path / "workflows" / wid / "recipe.json"
        path.write_text("{broken")
        response = client.post(f"/api/workflows/{wid}/reload")
        assert response.status_code == 422 and "无法打开" in response.text


def test_declared_dependency_hash_checked_by_unified_resolver():
    from hololab.gateway.preparation import resolve_recipe

    graph = WorkflowGraph(
        nodes=[GraphNode(id="a", algorithm_name="test", algorithm_version="1.0.0")],
        recipe_dependencies=[{"name": "test", "version": "1.0.0", "hash": "a" * 64}],
    )
    catalog = [
        {
            "name": "test",
            "version": "1.0.0",
            "params": {},
            "inputs": {},
            "publication": {"code_sha256": "b" * 64},
        }
    ]
    with pytest.raises(ValueError, match="包身份不匹配"):
        resolve_recipe(graph, catalog)


def test_local_cosmetic_patch_does_not_write_recipe_or_advance_revision(tmp_path):
    with TestClient(create_app(db_path=tmp_path / "test.sqlite")) as client:
        wid, before = create(client)
        path = tmp_path / "workflows" / wid / "recipe.json"
        raw = path.read_bytes()
        response = client.patch(
            f"/api/workflows/{wid}/graph-nodes/a/cosmetic", json={"preview_open": "out"}
        )
        assert response.status_code == 200, response.text
        after = client.get(f"/api/workflows/{wid}").json()
        assert after["graph"]["nodes"][0]["preview_open"] == "out"
        assert after["updated_ts"] == before["updated_ts"]
        assert path.read_bytes() == raw
