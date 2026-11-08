import json
from pathlib import Path

import pytest

from hololab.gateway.workflow_files import (
    WorkflowDocument,
    WorkflowFiles,
    read_document,
    serialize,
    write_document,
)
from hololab.gateway.workflows import GraphNode, WorkflowGraph, WorkflowStore
from hololab.persistence.db import open_database


@pytest.fixture(autouse=True)
def isolated_user_data(tmp_path, monkeypatch):
    monkeypatch.setenv("HOLOLAB_HOME", str(tmp_path / "user-data"))


def test_document_roundtrip_and_stability(tmp_path):
    graph = WorkflowGraph(
        nodes=[GraphNode(id=i, algorithm_name="p", algorithm_version="1") for i in ("z", "a")]
    )
    document = WorkflowDocument(id="id", name="中文", graph=graph)
    path = tmp_path / "one.workflow"
    write_document(path, document)
    assert path.read_bytes() == serialize(read_document(path))
    assert [n.id for n in read_document(path).graph.nodes] == ["a", "z"]
    assert path.read_text().startswith('{\n  "formatVersion": 1,')
    graph.nodes[0].params = {"z": 1, "a": {"z": 2, "a": 3}}
    other = document.model_copy(deep=True)
    other.graph.nodes[0].params = {"a": {"a": 3, "z": 2}, "z": 1}
    assert serialize(document) == serialize(other)


def test_future_version(tmp_path):
    path = tmp_path / "future.workflow"
    path.write_text(json.dumps(dict(formatVersion=2)))
    with pytest.raises(ValueError, match="更新版本"):
        read_document(path)


def test_atomic_failure_preserves_previous(tmp_path, monkeypatch):
    path = tmp_path / "one.workflow"
    document = WorkflowDocument(id="id", name="old", graph=WorkflowGraph())
    write_document(path, document)
    before = path.read_bytes()

    def fail(*args):
        raise OSError("rename failed")

    monkeypatch.setattr("hololab.gateway.workflow_files.os.replace", fail)
    with pytest.raises(OSError):
        write_document(path, document.model_copy(update={"name": "new"}))
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.asyncio
async def test_registry_file_priority_missing_fallback(tmp_path):
    db = await open_database(tmp_path / "db.sqlite")
    try:
        store = WorkflowStore(db)
        files = WorkflowFiles(store, tmp_path)
        store.workflow_files = files
        await store._save_cached(workflow_id="id", name="cached", graph=WorkflowGraph())
        assert (await store.get_draft("id")).name == "cached"
        assert not list(tmp_path.glob("*.workflow"))
        path = tmp_path / "one.workflow"
        write_document(path, WorkflowDocument(id="id", name="file", graph=WorkflowGraph()))
        await files.scan()
        assert (await store.get_draft("id")).name == "file"
        moved = tmp_path / "renamed.workflow"
        path.rename(moved)
        await files.scan()
        assert Path((await files.entries())[0][1]) == moved
        moved.unlink()
        assert (await store.get_draft("id")).name == "cached"
    finally:
        await db.close()


def test_api_content_dispatch_and_recipe_coexist(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from hololab.gateway.app import create_app

    root = tmp_path / "documents"
    root.mkdir()
    path = root / "manual.workflow"
    graph = WorkflowGraph(
        nodes=[GraphNode(id="node", algorithm_name="p", algorithm_version="1.0.0", params={"x": 1})]
    )
    write_document(path, WorkflowDocument(id="manual", name="manual", graph=graph))

    async def dispatch(app, **kwargs):
        snapshot = await app.state.workflows.create_snapshot(
            workflow_id=kwargs["workflow_id"], graph=kwargs["graph"]
        )
        return {"snapshot_id": snapshot.snapshot_id}

    monkeypatch.setattr("hololab.gateway.app.dispatch_graph_node", dispatch)
    with TestClient(create_app(db_path=tmp_path / "db.sqlite", workflows_dir=root)) as client:
        got = client.get("/api/workflows/manual")
        assert got.status_code == 200, got.text
        detail = got.json()
        assert detail["path"] == str(path)
        assert client.get("/api/workflows").json()[0]["id"] == "manual"
        graph.nodes[0].params["x"] = 2
        content = dict(
            name="manual", graph=graph.model_dump(), expected_mtime=detail["mtime"], base_rev=0
        )
        assert client.put("/api/workflows/manual/content", json=content).status_code == 200
        assert client.put("/api/workflows/manual/content", json=content).status_code == 409
        dispatched = client.post("/api/workflows/manual/dispatch/node")
        assert dispatched.status_code == 200, dispatched.text
        snapshot = client.portal.call(
            client.app.state.workflows.get_snapshot, dispatched.json()["snapshot_id"]
        )
        assert snapshot.graph == graph
        before = path.read_bytes()
        saved = client.post("/api/workflows", json=dict(name="legacy", graph=graph.model_dump()))
        assert saved.status_code == 200, saved.text
        wid = saved.json()["workflow_id"]
        recipe = tmp_path / "workflows" / wid / "recipe.json"
        assert recipe.exists()
        recipe_before = recipe.read_bytes()
        assert client.post("/api/workflows/reload").status_code == 200
        assert recipe.read_bytes() == recipe_before
        assert path.read_bytes() == before
        assert len(list(root.glob("*.workflow"))) == 1
        assert client.get(f"/api/workflows/{wid}").status_code == 200
        fallback_graph = client.portal.call(client.app.state.workflows._get_cached, wid).graph
        fallback = client.post(f"/api/workflows/{wid}/dispatch/node")
        assert fallback.status_code == 200, fallback.text
        snapshot = client.portal.call(
            client.app.state.workflows.get_snapshot, fallback.json()["snapshot_id"]
        )
        assert snapshot.graph == fallback_graph
        assert client.delete("/api/workflows/manual").status_code == 200
        assert not path.exists()
        assert (
            client.portal.call(
                client.app.state.workflows.get_snapshot, dispatched.json()["snapshot_id"]
            )
            is not None
        )


def test_migration_cli_only_temporary_data(tmp_path):
    import asyncio

    from typer.testing import CliRunner

    from hololab.cli import app

    database = tmp_path / "db.sqlite"

    async def seed():
        db = await open_database(database)
        try:
            store = WorkflowStore(db)
            for wid in ("first", "second"):
                await store.save_draft(workflow_id=wid, name="same", graph=WorkflowGraph())
        finally:
            await db.close()

    asyncio.run(seed())
    root = tmp_path / "export"
    result = CliRunner().invoke(
        app, ["workflow", "migrate-files", "--db-path", str(database), "--workflows-dir", str(root)]
    )
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["success"] == 2 and report["empty"] == 2
    assert len(report["renamed"]) == 1
    assert {p.name for p in root.glob("*.workflow")} == {"same.workflow", "same-2.workflow"}
    assert {read_document(p).id for p in root.glob("*.workflow")} == {"first", "second"}
    repeated = CliRunner().invoke(
        app, ["workflow", "migrate-files", "--db-path", str(database), "--workflows-dir", str(root)]
    )
    assert repeated.exit_code == 0, repeated.output
    assert json.loads(repeated.output)["skipped"] == 2


@pytest.mark.asyncio
async def test_config_root_reload_and_watcher(tmp_path):
    import asyncio

    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    db = await open_database(tmp_path / "db.sqlite")
    files = WorkflowFiles(WorkflowStore(db), first)
    task = asyncio.create_task(files.watch())
    try:
        assert await files.sync_root(second)
        await asyncio.sleep(0.1)
        write_document(
            second / "new.workflow", WorkflowDocument(id="new", name="new", graph=WorkflowGraph())
        )
        for _ in range(60):
            if await files.entries():
                break
            await asyncio.sleep(0.1)
        assert (await files.entries())[0][0] == "new"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await db.close()


@pytest.mark.parametrize(
    "raw",
    [
        "[]",
        "null",
        '{"formatVersion":true}',
        '{"formatVersion":0}',
        '{"formatVersion":1.5}',
        '{"formatVersion":1}',
    ],
)
def test_invalid_documents_are_rejected(tmp_path, raw):
    path = tmp_path / "invalid.workflow"
    path.write_text(raw)
    with pytest.raises(ValueError):
        read_document(path)


def test_atomic_write_does_not_remove_another_writers_temporary(tmp_path):
    import os

    path = tmp_path / "new.workflow"
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    temporary.write_bytes(b"another writer")
    with pytest.raises(FileExistsError):
        write_document(path, WorkflowDocument(id="id", name="name", graph=WorkflowGraph()))
    assert temporary.read_bytes() == b"another writer"


@pytest.mark.asyncio
async def test_duplicate_ids_invalid_files_and_recovery(tmp_path):
    db = await open_database(tmp_path / "db.sqlite")
    try:
        store = WorkflowStore(db)
        files = WorkflowFiles(store, tmp_path)
        doc = WorkflowDocument(id="id", name="name", graph=WorkflowGraph())
        write_document(tmp_path / "first.workflow", doc)
        write_document(tmp_path / "second.workflow", doc)
        (tmp_path / "bad.workflow").write_text("not JSON")
        assert (await files.scan())["duplicates"] == ["id"]
        with pytest.raises(ValueError, match="duplicate"):
            await files.resolve("id")
        (tmp_path / "second.workflow").unlink()
        assert (await files.scan())["loaded"] == 1
        assert (await files.resolve("id")).name == "name"
        (tmp_path / "first.workflow").unlink()
        assert (await files.resolve("id")).name == "name"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_node_config_disk_hot_reload(tmp_path):
    from unittest.mock import AsyncMock

    from hololab.node.config import NodeConfig, write_node_config
    from hololab.node.runtime import NodeRuntime

    path = tmp_path / "config.yaml"
    cfg = NodeConfig(workflows_dir=tmp_path / "first")
    runtime = NodeRuntime(cfg, config_path=path)
    runtime._restart_file_server = AsyncMock()
    runtime._restart_pack_watcher = AsyncMock()
    write_node_config(cfg.model_copy(update={"workflows_dir": tmp_path / "second"}), path)
    result = await runtime.sync_config_from_disk(trigger="test")
    assert "workflows_dir" in result["applied_fields"]
    assert "workflows_dir" not in result["restart_required_fields"]
    assert runtime._config.workflows_dir == tmp_path / "second"
    runtime._restart_file_server.assert_not_awaited()
    runtime._restart_pack_watcher.assert_not_awaited()


def test_gateway_config_hot_reload(tmp_path):
    import time

    from fastapi.testclient import TestClient

    from hololab.gateway.app import create_app
    from hololab.node.config import NodeConfig, write_node_config

    root = tmp_path / "first"
    root.mkdir()
    config = tmp_path / "config.yaml"
    write_node_config(NodeConfig(workflows_dir=root), config)
    with TestClient(
        create_app(db_path=tmp_path / "db.sqlite", workflow_config_path=config)
    ) as client:
        new = tmp_path / "new"
        new.mkdir()
        write_document(
            new / "new.workflow", WorkflowDocument(id="new", name="new", graph=WorkflowGraph())
        )
        write_node_config(NodeConfig(workflows_dir=new), config)
        for _ in range(50):
            if any(row["id"] == "new" for row in client.get("/api/workflows").json()):
                break
            time.sleep(0.1)
        assert client.app.state.workflow_files.root == new
        assert client.get("/api/workflows/new").status_code == 200


def test_new_relink_missing_and_identity_guards(tmp_path):
    from fastapi.testclient import TestClient

    from hololab.gateway.app import create_app

    with TestClient(create_app(db_path=tmp_path / "db.sqlite", workflows_dir=tmp_path)) as client:
        created = client.post(
            "/api/workflow-files", json=dict(parent_dir=str(tmp_path), name="new")
        )
        assert created.status_code == 200, created.text
        wid, path = created.json()["id"], Path(created.json()["path"])
        moved = tmp_path / "moved.workflow"
        path.rename(moved)
        assert client.get(f"/api/workflows/{wid}").json()["missing"] is True
        assert (
            client.post(f"/api/workflows/{wid}/relink", json={"path": str(moved)}).status_code
            == 200
        )
        assert client.get(f"/api/workflows/{wid}").json()["missing"] is False
        write_document(
            moved, WorkflowDocument(id="different", name="different", graph=WorkflowGraph())
        )
        assert client.delete(f"/api/workflows/{wid}").status_code == 409
        assert moved.exists()
        body = dict(name="bad", graph={}, expected_mtime=moved.stat().st_mtime, base_rev=0)
        assert client.put(f"/api/workflows/{wid}/content", json=body).status_code == 409
        assert (
            client.post(
                "/api/workflow-files", json=dict(parent_dir=str(tmp_path), name="../bad")
            ).status_code
            == 422
        )


def test_move_workflow_file_updates_registry_and_broadcasts(tmp_path):
    from fastapi.testclient import TestClient

    from hololab.gateway.app import create_app

    destination_dir = tmp_path / "moved"
    destination_dir.mkdir()
    with TestClient(create_app(db_path=tmp_path / "db.sqlite", workflows_dir=tmp_path)) as client:
        created = client.post(
            "/api/workflow-files", json={"parent_dir": str(tmp_path), "name": "original"}
        )
        assert created.status_code == 200, created.text
        workflow_id = created.json()["id"]
        source = Path(created.json()["path"])
        with client.websocket_connect("/ws/frontend") as websocket:
            moved = client.post(
                f"/api/workflows/{workflow_id}/move",
                json={"parent_dir": str(destination_dir), "name": "renamed.workflow"},
            )
            assert moved.status_code == 200, moved.text
            destination = destination_dir / "renamed.workflow"
            assert moved.json()["path"] == str(destination)
            assert destination.exists() and not source.exists()
            assert client.get("/api/workflows").json()[0]["path"] == str(destination)
            detail = client.get(f"/api/workflows/{workflow_id}")
            assert detail.status_code == 200 and detail.json()["path"] == str(destination)
            frame = websocket.receive_json()
            assert frame["kind"] == "workflow_changed"
            assert frame["payload"]["id"] == workflow_id
            assert frame["payload"]["path"] == str(destination)

        occupied = destination_dir / "occupied.workflow"
        write_document(
            occupied, WorkflowDocument(id="occupied", name="occupied", graph=WorkflowGraph())
        )
        exists = client.post(
            f"/api/workflows/{workflow_id}/move",
            json={"parent_dir": str(destination_dir), "name": "occupied.workflow"},
        )
        assert exists.status_code == 409
        assert "already exists" in exists.json()["detail"]
        destination.unlink()
        missing = client.post(
            f"/api/workflows/{workflow_id}/move",
            json={"parent_dir": str(destination_dir), "name": "again.workflow"},
        )
        assert missing.status_code == 409
        assert "source workflow file is missing" in missing.json()["detail"]
