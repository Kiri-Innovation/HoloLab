"""Stage-one contracts: portable v1 documents and gateway-local assignments."""

import asyncio
import copy
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from typer.testing import CliRunner

from hololab.gateway.app import create_app
from hololab.gateway.device_bindings import (
    DeviceBindingConflict,
    DeviceBindingPatch,
    DeviceBindingStore,
)
from hololab.gateway.migrate_device_bindings import migrate
from hololab.gateway.workflow_files import WorkflowDocument, read_document, serialize
from hololab.gateway.workflows import GraphNode, WorkflowGraph, WorkflowStore
from hololab.persistence.db import open_database


@pytest.fixture(autouse=True)
def isolated_user_data(tmp_path, monkeypatch):
    monkeypatch.setenv("HOLOLAB_HOME", str(tmp_path / "user-data"))


def algorithm(node_id="a", **kwargs):
    return dict(id=node_id, algorithm_name="p", algorithm_version="1.0.0", **kwargs)


def document():
    return dict(
        formatVersion=1,
        id="wf",
        name="Example",
        editor={"zoom": 1},
        graph=dict(
            frontend_private={"collapsed": ["a"]},
            null_private=None,
            nodes=[
                algorithm(
                    device="gpu",
                    assigned_node_id="physical",
                    private={"color": "red"},
                    position={"x": 1, "y": 2, "snap": True},
                ),
                algorithm("b"),
            ],
            edges=[
                dict(
                    id="edge",
                    source="a",
                    sourceHandle="out",
                    target="b",
                    targetHandle="in",
                    private="edge state",
                )
            ],
        ),
    )


def test_v1_device_defaults_exclusions_and_legacy_read():
    graph = WorkflowGraph(
        nodes=[
            GraphNode(**algorithm(assigned_node_id="physical")),
            GraphNode(id="view", kind="view", view_type="artifact-preview"),
            GraphNode(
                id="constant",
                kind="intrinsic",
                operation="constant-float-array",
                params={"items": [1]},
            ),
            GraphNode(
                id="vec",
                kind="intrinsic",
                operation="constant-vec3-array",
                params={"x": [1], "y": [2], "scale": [1]},
            ),
        ]
    )
    assert graph.nodes[0].device == "default"
    assert graph.nodes[0].assigned_node_id == "physical"
    raw = json.loads(serialize(WorkflowDocument(id="wf", name="Example", graph=graph)))
    assert raw["formatVersion"] == 1
    nodes = {node["id"]: node for node in raw["graph"]["nodes"]}
    assert nodes["a"]["device"] == "default"
    assert "assigned_node_id" not in nodes["a"]
    assert all("device" not in nodes[key] for key in ("view", "constant", "vec"))
    legacy = WorkflowDocument.model_validate(document())
    assert legacy.graph.nodes[0].assigned_node_id == "physical"


@pytest.mark.parametrize("value", [None, 3, True, ""])
def test_device_requires_nonempty_string(value):
    with pytest.raises(ValidationError):
        GraphNode(**algorithm(device=value))


def test_full_content_save_keeps_unknown_and_omitted_device(tmp_path):
    root = tmp_path / "workflows"
    root.mkdir()
    path = root / "example.workflow"
    path.write_text(json.dumps(document()))
    with TestClient(create_app(db_path=tmp_path / "db.sqlite", workflows_dir=root)) as client:
        got = client.get("/api/workflows/wf").json()
        old_graph = copy.deepcopy(document()["graph"])
        old_graph.pop("frontend_private")
        old_graph.pop("null_private")
        for node in old_graph["nodes"]:
            node.pop("device", None)
            node.pop("private", None)
            node["position"] = {"x": 10, "y": 20}
            node["params"] = {}
        old_graph["edges"][0].pop("private")
        result = client.put(
            "/api/workflows/wf/content",
            json=dict(
                name="Edited", graph=old_graph, base_rev=got["rev"], expected_mtime=got["mtime"]
            ),
        )
        assert result.status_code == 200, result.text
        saved = json.loads(path.read_text())
        assert saved["formatVersion"] == 1
        assert saved["editor"] == {"zoom": 1}
        assert saved["graph"]["frontend_private"] == {"collapsed": ["a"]}
        assert "null_private" in saved["graph"] and saved["graph"]["null_private"] is None
        assert saved["graph"]["nodes"][0]["device"] == "gpu"
        assert saved["graph"]["nodes"][0]["private"] == {"color": "red"}
        assert saved["graph"]["nodes"][0]["position"] == {"x": 10, "y": 20, "snap": True}
        assert saved["graph"]["edges"][0]["private"] == "edge state"
        assert "assigned_node_id" not in path.read_text()
        # Explicit device edits and node/edge deletions remain possible.
        graph = saved["graph"]
        graph["nodes"] = [graph["nodes"][0]]
        graph["nodes"][0]["device"] = "default"
        graph["edges"] = []
        result = client.put(
            "/api/workflows/wf/content",
            json=dict(
                name="Edited",
                graph=graph,
                base_rev=result.json()["rev"],
                expected_mtime=result.json()["mtime"],
            ),
        )
        assert result.status_code == 200, result.text
        final = read_document(path).graph
        assert len(final.nodes) == 1 and not final.edges
        assert final.nodes[0].device == "default"


@pytest.mark.asyncio
async def test_cached_draft_save_keeps_extensions(tmp_path):
    db = await open_database(tmp_path / "db.sqlite")
    try:
        store = WorkflowStore(db)
        await store.save_draft(
            workflow_id="wf", name="Old", graph=WorkflowGraph.model_validate(document()["graph"])
        )
        old_client = WorkflowGraph(nodes=[GraphNode(**algorithm())])
        row = await store.save_draft(workflow_id="wf", name="New", graph=old_client)
        assert row.graph.nodes[0].device == "gpu"
        assert row.graph.nodes[0].model_extra["private"] == {"color": "red"}
        assert row.graph.model_extra["null_private"] is None
        assert len(row.graph.nodes) == 1 and not row.graph.edges
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_binding_crud_revision_isolation_and_concurrent_cas(tmp_path):
    path = tmp_path / "db.sqlite"
    db = await open_database(path)
    try:
        workflows = WorkflowStore(db)
        await workflows.save_draft(
            workflow_id="wf",
            name="Example",
            graph=WorkflowGraph(nodes=[GraphNode(**algorithm(assigned_node_id="legacy"))]),
        )
        before = await workflows.get_draft("wf")
        async with db.read() as conn:
            legacy = await (await conn.execute("SELECT * FROM workflow_bindings")).fetchall()
        store = DeviceBindingStore(db)
        assert await store.get("wf") == dict(workflow_id="wf", revision=0, bindings={})
        patch = DeviceBindingPatch(base_revision=0, bindings={"default": "physical"})
        outcomes = await asyncio.gather(
            store.patch("wf", patch), store.patch("wf", patch), return_exceptions=True
        )
        assert sum(isinstance(x, DeviceBindingConflict) for x in outcomes) == 1
        assert (await store.get("wf"))["revision"] == 1
        changed = await store.patch(
            "wf",
            DeviceBindingPatch(base_revision=1, bindings={"default": "other", "gpu": "physical"}),
        )
        assert changed["revision"] == 2 and len(changed["bindings"]) == 2
        removed = await store.patch(
            "wf", DeviceBindingPatch(base_revision=2, bindings={"default": None, "gpu": None})
        )
        assert removed["revision"] == 3 and removed["bindings"] == {}
        after = await workflows.get_draft("wf")
        assert before == after
        async with db.read() as conn:
            assert (
                legacy == await (await conn.execute("SELECT * FROM workflow_bindings")).fetchall()
            )
        with pytest.raises(KeyError):
            await store.get("missing")
    finally:
        await db.close()
    db = await open_database(path)
    try:
        assert (await DeviceBindingStore(db).get("wf"))["revision"] == 3
        await db.write(lambda conn: conn.execute("DELETE FROM workflows WHERE workflow_id='wf'"))
        async with db.read() as conn:
            assert not await (
                await conn.execute("SELECT * FROM workflow_device_binding_revisions")
            ).fetchall()
    finally:
        await db.close()


def test_binding_api_conflicts_validation_and_file_isolation(tmp_path):
    root = tmp_path / "workflows"
    root.mkdir()
    path = root / "example.workflow"
    path.write_text(json.dumps(document()))
    before = path.read_bytes()
    with TestClient(create_app(db_path=tmp_path / "db.sqlite", workflows_dir=root)) as client:
        endpoint = "/api/workflows/wf/device-bindings"
        detail = client.get("/api/workflows/wf").json()
        assert client.get(endpoint).json()["revision"] == 0
        result = client.patch(endpoint, json=dict(base_revision=0, bindings={"gpu": "machine"}))
        assert result.status_code == 200, result.text
        assert result.json()["revision"] == 1
        conflict = client.patch(endpoint, json=dict(base_revision=0, bindings={"gpu": "other"}))
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["current"] == result.json()
        for body in (
            {"bindings": {}},
            {"base_revision": True, "bindings": {}},
            {"base_revision": -1, "bindings": {}},
            {"base_revision": 1, "bindings": {"": "x"}},
            {"base_revision": 1, "bindings": {"gpu": 3}},
        ):
            assert client.patch(endpoint, json=body).status_code == 422
        assert client.get("/api/workflows/missing/device-bindings").status_code == 404
        assert (
            client.patch(
                "/api/workflows/missing/device-bindings", json=dict(base_revision=0, bindings={})
            ).status_code
            == 404
        )
        assert path.read_bytes() == before
        assert client.get("/api/workflows/wf").json()["rev"] == detail["rev"]
        assert client.patch(
            endpoint, json=dict(base_revision=1, bindings={"gpu": None})
        ).json() == dict(workflow_id="wf", revision=2, bindings={})


def seed_migration(tmp_path, multiple):
    database = tmp_path / "db.sqlite"
    root = tmp_path / "workflows"
    root.mkdir()
    graph = {
        "nodes": [
            algorithm("a", assigned_node_id="machine-a"),
            algorithm("b", assigned_node_id="machine-b" if multiple else "machine-a"),
            algorithm("c", assigned_node_id="machine-a"),
        ],
        "edges": [],
    }

    async def seed():
        db = await open_database(database)
        try:
            await WorkflowStore(db).save_draft(
                workflow_id="wf", name="Example", graph=WorkflowGraph.model_validate(graph)
            )
        finally:
            await db.close()

    asyncio.run(seed())
    path = root / "example.workflow"
    path.write_text(
        json.dumps(
            dict(formatVersion=1, id="wf", name="Example", graph=graph, private={"keep": True})
        )
    )
    return database, root, path


@pytest.mark.parametrize("multiple", [False, True])
def test_migration_cli_dry_run_backup_and_idempotence(tmp_path, multiple):
    from hololab.cli import app

    database, root, path = seed_migration(tmp_path, multiple)
    before_db, before_file = database.read_bytes(), path.read_bytes()
    args = [
        "workflow",
        "migrate-devices",
        "--db-path",
        str(database),
        "--workflows-dir",
        str(root),
        "--dry-run",
    ]
    first = CliRunner().invoke(app, args)
    assert first.exit_code == 0, first.output
    second = CliRunner().invoke(app, args)
    assert second.exit_code == 0 and first.output == second.output
    assert json.loads(first.output)["changed"] == 1
    assert database.read_bytes() == before_db and path.read_bytes() == before_file
    assert not list(tmp_path.glob("logical-device-backup-*"))
    backup = tmp_path / "backup"
    result = migrate(database, root, dry_run=False, backup_dir=backup)
    assert result["changed"] == 1
    assert (backup / "0.workflow").read_bytes() == before_file
    assert (backup / "plan.json").exists()
    with sqlite3.connect(backup / "gateway.sqlite") as conn:
        assert conn.execute("SELECT COUNT(*) FROM workflow_device_bindings").fetchone()[0] == 0
    assert json.loads(path.read_text())["private"] == {"keep": True}
    assert "assigned_node_id" not in path.read_text()
    nodes = {n.id: n.device for n in read_document(path).graph.nodes}
    assert nodes["a"] == nodes["c"] == "default"
    assert nodes["b"] == ("device-2" if multiple else "default")
    with sqlite3.connect(database) as conn:
        bindings = dict(conn.execute("SELECT logical_id,node_id FROM workflow_device_bindings"))
        assert bindings == (
            {"default": "machine-a", "device-2": "machine-b"}
            if multiple
            else {"default": "machine-a"}
        )
        assert (
            conn.execute("SELECT revision FROM workflow_device_binding_revisions").fetchone()[0]
            == 1
        )
        legacy = conn.execute("SELECT bindings_json FROM workflow_bindings").fetchone()[0]
        assert json.loads(legacy)["a"] == "machine-a"
    migrated_db, migrated_file = database.read_bytes(), path.read_bytes()
    assert migrate(database, root)["changed"] == 0
    assert migrate(database, root, dry_run=False)["changed"] == 0
    assert database.read_bytes() == migrated_db and path.read_bytes() == migrated_file


@pytest.mark.asyncio
async def test_recipe_authority_retains_device_and_cached_extensions(tmp_path):
    db = await open_database(tmp_path / "db.sqlite")
    try:
        store = WorkflowStore(db, recipe_root=tmp_path / "recipes")
        raw = document()["graph"]
        raw["nodes"][0]["private_null"] = None
        first = await store.save_draft(
            workflow_id="wf", name="Example", graph=WorkflowGraph.model_validate(raw)
        )
        saved = await store.save_draft(
            workflow_id="wf",
            name="Edited",
            graph=WorkflowGraph(nodes=[GraphNode(**algorithm())]),
            base_updated_ts=first.updated_ts,
        )
        assert saved.graph.nodes[0].device == "gpu"
        assert saved.graph.nodes[0].model_extra["private_null"] is None
        assert saved.graph.model_extra["frontend_private"] == {"collapsed": ["a"]}
        recipe = json.loads((tmp_path / "recipes/wf/recipe.json").read_text())
        assert recipe["graph"]["nodes"][0]["device"] == "gpu"
        assert "private_null" not in recipe["graph"]["nodes"][0]
    finally:
        await db.close()


def test_migration_recovers_originals_after_file_failure(tmp_path, monkeypatch):
    database, root, path = seed_migration(tmp_path, True)
    before = path.read_bytes()

    def fail_write(*args):
        # Simulate a partially completed cross-file write.
        path.write_text("partial")
        raise OSError("simulated disk failure")

    monkeypatch.setattr("hololab.gateway.workflow_files.write_document", fail_write)
    with pytest.raises(OSError, match="simulated"):
        migrate(database, root, dry_run=False, backup_dir=tmp_path / "backup")
    assert path.read_bytes() == before
    with sqlite3.connect(database) as conn:
        assert not conn.execute("SELECT * FROM workflow_device_bindings").fetchall()
        assert not conn.execute("SELECT * FROM workflow_device_binding_revisions").fetchall()
    assert migrate(database, root)["changed"] == 1


def test_migration_cache_only_and_existing_binding_conflict(tmp_path):
    database, root, path = seed_migration(tmp_path, False)
    path.unlink()
    # The old overlay, not draft_json, is the physical source for DB-only drafts.
    result = migrate(database, root, dry_run=False)
    assert result["changed"] == 1 and result["workflows"][0]["path"] is None
    assert migrate(database, root)["changed"] == 0
    with sqlite3.connect(database) as conn:
        graph = json.loads(conn.execute("SELECT draft_json FROM workflows").fetchone()[0])
        assert all(n["device"] == "default" for n in graph["nodes"])
        conn.execute("UPDATE workflow_device_bindings SET node_id='another'")
    before = database.read_bytes()
    with pytest.raises(ValueError, match="conflicting binding"):
        migrate(database, root, dry_run=False)
    assert database.read_bytes() == before


def test_migration_pre_stage_one_schema_dry_run_does_not_upgrade(tmp_path):
    database, root, path = seed_migration(tmp_path, True)
    with sqlite3.connect(database) as conn:
        conn.execute("DROP TABLE workflow_device_bindings")
        conn.execute("DROP TABLE workflow_device_binding_revisions")
        conn.execute("UPDATE schema_version SET version=32")
    before = database.read_bytes(), path.read_bytes()
    assert migrate(database, root)["changed"] == 1
    assert (database.read_bytes(), path.read_bytes()) == before
    migrate(database, root, dry_run=False)
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 33
        assert conn.execute("SELECT COUNT(*) FROM workflow_device_bindings").fetchone()[0] == 2
    assert migrate(database, root)["changed"] == 0
