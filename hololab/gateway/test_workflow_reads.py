"""Regression tests for the workflow list and versioned package usage index."""

import json

import pytest
from fastapi.testclient import TestClient

from hololab.gateway.app import create_app
from hololab.gateway.recipe_files import export_recipe
from hololab.gateway.workflows import WorkflowGraph


def graph(name="kiri/example", version="1.0.0"):
    return {
        "nodes": [
            {
                "id": "one",
                "algorithm_name": name,
                "algorithm_version": version,
                "assigned_node_id": "machine",
                "params": {},
                "position": {"x": 0, "y": 0},
            }
        ],
        "edges": [],
    }


@pytest.mark.parametrize("authority", ["db", "file"])
def test_usage_save_delete_import_and_identity(tmp_path, monkeypatch, authority):
    monkeypatch.setenv("HOLOLAB_RECIPE_AUTHORITY", authority)
    with TestClient(create_app(db_path=tmp_path / "test.sqlite")) as client:
        path = "/api/packs/kiri/example@1.0.0/usage"
        assert client.get(path).json() == []
        raw = graph("example")
        raw["nodes"].append({**raw["nodes"][0], "id": "two"})
        saved = client.post("/api/workflows", json={"name": "Example", "graph": raw}).json()
        wid = saved["workflow_id"]
        first = client.get(path).json()
        assert [w["workflow_id"] for w in first] == [wid]
        assert len(first[0]["graph"]["nodes"]) == 2
        assert first[0]["graph"]["nodes"][0]["assigned_node_id"] == "machine"
        assert client.get(path).json() == first
        assert client.get("/api/packs/example@1.0.0/usage").json() == first
        assert client.get("/api/packs/community/example@1.0.0/usage").json() == []
        assert client.get("/api/packs/kiri/example@2.0.0/usage").json() == []
        changed = client.post(
            "/api/workflows",
            json={
                "workflow_id": wid,
                "name": "Changed",
                "graph": graph("community/example"),
                "base_updated_ts": saved["updated_ts"],
            },
        )
        assert changed.status_code == 200, changed.text
        assert client.get(path).json() == []
        assert len(client.get("/api/packs/community/example@1.0.0/usage").json()) == 1
        document = export_recipe(WorkflowGraph.model_validate(graph()), "Imported", []).model_dump()
        imported = client.post("/api/recipes/import", json=document)
        assert imported.status_code == 200, imported.text
        iid = imported.json()["workflow_id"]
        assert [w["workflow_id"] for w in client.get(path).json()] == [iid]
        assert client.delete("/api/workflows/" + iid).status_code == 200
        assert client.get(path).json() == []
        # A conflict does not invalidate committed references with the rejected graph.
        assert (
            client.post(
                "/api/workflows",
                json={
                    "workflow_id": wid,
                    "name": "Rejected",
                    "graph": graph(),
                    "base_updated_ts": saved["updated_ts"],
                },
            ).status_code
            == 409
        )
        assert client.get(path).json() == []
        assert client.get("/api/packs/no-version/usage").status_code == 422


def test_usage_cache_and_external_recipe_edit(tmp_path, monkeypatch):
    monkeypatch.setenv("HOLOLAB_RECIPE_AUTHORITY", "file")
    with TestClient(create_app(db_path=tmp_path / "test.sqlite")) as client:
        saved = client.post("/api/workflows", json={"name": "Example", "graph": graph()}).json()
        wid = saved["workflow_id"]
        path = "/api/packs/kiri/example@1.0.0/usage"
        assert len(client.get(path).json()) == 1
        from hololab.gateway import recipe_files

        original = recipe_files.diagnostics

        def forbidden(*args):
            raise AssertionError("cache hit reran diagnostics")

        monkeypatch.setattr(recipe_files, "diagnostics", forbidden)
        assert len(client.get(path).json()) == 1
        monkeypatch.setattr(recipe_files, "diagnostics", original)
        recipe = tmp_path / "workflows" / wid / "recipe.json"
        data = json.loads(recipe.read_text())
        data["graph"]["nodes"][0]["algorithm_name"] = "community/example"
        recipe.write_text(json.dumps(data))
        assert client.get(path).json() == []
        assert len(client.get("/api/packs/community/example@1.0.0/usage").json()) == 1
        recipe.unlink()
        assert client.get(path).status_code == 422


def test_list_never_deserializes_snapshots_or_jobs(tmp_path, monkeypatch):
    monkeypatch.setenv("HOLOLAB_RECIPE_AUTHORITY", "db")
    with TestClient(create_app(db_path=tmp_path / "test.sqlite")) as client:
        saved = client.post("/api/workflows", json={"name": "Example", "graph": graph()}).json()

        async def forbidden(*args):
            raise AssertionError("list read full historical graphs/jobs")

        monkeypatch.setattr(client.app.state.workflows, "list_snapshots_for_workflow", forbidden)
        monkeypatch.setattr(client.app.state.jobs_store, "list_by_snapshot", forbidden)
        rows = client.get("/api/workflows").json()
        assert rows == [
            {
                "workflow_id": saved["workflow_id"],
                "name": "Example",
                "node_count": 1,
                "created_ts": saved["updated_ts"],
                "updated_ts": saved["updated_ts"],
                "last_run": None,
            }
        ]


def test_scoped_catalog_and_usage_diagnostics_invalidation(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from hololab.protocol.messages import PackInventoryEntry

    monkeypatch.setenv("HOLOLAB_RECIPE_AUTHORITY", "db")
    with TestClient(create_app(db_path=tmp_path / "test.sqlite")) as client:
        registry = client.app.state.registry
        session = SimpleNamespace(
            node_id="machine",
            pack_dirs=[],
            packs=[
                PackInventoryEntry(name="kiri/example", version="1.0.0", manifest_hash="a"),
                PackInventoryEntry(name="community/example", version="1.0.0", manifest_hash="b"),
            ],
        )
        registry._sessions["machine"] = session
        full = client.get("/api/pack-catalog").json()
        assert len(full) == 2
        assert client.get("/api/pack-catalog?key=kiri/example@1.0.0").json() == [
            p for p in full if p["name"] == "kiri/example"
        ]
        assert client.get("/api/pack-catalog?key=invalid").status_code == 422
        assert (
            client.post("/api/workflows", json={"name": "Example", "graph": graph()}).status_code
            == 200
        )
        original = registry.catalog_json
        calls = []

        def catalog(*, keys=None):
            calls.append(keys)
            return original(keys=keys)

        monkeypatch.setattr(registry, "catalog_json", catalog)
        path = "/api/packs/kiri/example@1.0.0/usage"
        first = client.get(path).json()
        assert not any(i["code"] == "missing_package" for i in first[0]["check"]["issues"])
        assert calls == [{("kiri/example", "1.0.0")}]
        assert client.get(path).json() == first
        assert len(calls) == 1  # cache hit does not parse/copy unrelated catalog manifests
        session.packs = session.packs[1:]
        changed = client.get(path).json()
        assert any(i["code"] == "missing_package" for i in changed[0]["check"]["issues"])
        assert len(calls) == 2
