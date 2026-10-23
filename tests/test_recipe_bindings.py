import json

import pytest

from hololab.gateway.recipe import Bindings, compose_graph, split_graph
from hololab.gateway.workflows import GraphNode, WorkflowConflict, WorkflowGraph, WorkflowStore
from hololab.persistence.db import open_database


def graph():
    return WorkflowGraph(
        nodes=[
            GraphNode(
                id="a",
                algorithm_name="x",
                algorithm_version="1.0.0",
                assigned_node_id="device",
                params={"path": "/data", "null": None, "empty": [], "text": ""},
            )
        ]
    )


def test_split_roundtrip():
    g = graph()
    recipe, bindings, view = split_graph(g)
    assert compose_graph(recipe, bindings, view) == g
    unbound = compose_graph(recipe, Bindings({}), view)
    assert unbound.nodes[0].assigned_node_id is None
    assert unbound.nodes[0].params == g.nodes[0].params
    assert g.nodes[0].assigned_node_id == "device"


@pytest.mark.asyncio
async def test_lazy_migration_and_downgrade(tmp_path):
    db = await open_database(tmp_path / "test.sqlite")
    try:
        store = WorkflowStore(db)
        raw = graph().model_dump_json()

        async def seed(c):
            await c.execute("INSERT INTO workflows VALUES ('w','old',?,1,1)", (raw,))

        await db.write(seed)
        old = await store.get_draft("w")
        async with db.read() as c:
            assert (await (await c.execute("SELECT count(*) FROM workflow_bindings")).fetchone())[
                0
            ] == 0
        saved = await store.save_draft(
            workflow_id="w", name="old", graph=old.graph, base_updated_ts=1
        )
        async with db.read() as c:
            data = json.loads(
                (await (await c.execute("SELECT draft_json FROM workflows")).fetchone())[0]
            )
            assert "assigned_node_id" not in data["nodes"][0]
        assert saved.graph == old.graph
        with pytest.raises(WorkflowConflict):
            await store.save_draft(workflow_id="w", name="bad", graph=graph(), base_updated_ts=1)
        await store.restore_legacy_bindings("w")
        assert (await store.get_draft("w")).graph == old.graph
        await store.save_draft(workflow_id="w", name="again", graph=old.graph)
        assert (await store.get_draft("w")).graph == old.graph

    finally:
        await db.close()


@pytest.mark.asyncio
async def test_binding_write_failure_rolls_back_whole_save(tmp_path):
    db = await open_database(tmp_path / "failure.sqlite")
    try:
        store = WorkflowStore(db)
        first = await store.save_draft(workflow_id=None, name="original", graph=graph())

        async def trigger(c):
            await c.execute(
                "CREATE TRIGGER reject_binding BEFORE UPDATE ON workflow_bindings BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
            )

        await db.write(trigger)
        changed = graph()
        changed.nodes[0].assigned_node_id = "other"
        with pytest.raises(Exception, match="injected failure"):
            await store.save_draft(
                workflow_id=first.workflow_id,
                name="changed",
                graph=changed,
                base_updated_ts=first.updated_ts,
            )
        restored = await store.get_draft(first.workflow_id)
        assert restored.name == "original"
        assert restored.updated_ts == first.updated_ts
        assert restored.graph == first.graph
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_concurrent_saves_have_one_winner(tmp_path):
    import asyncio

    db = await open_database(tmp_path / "concurrent.sqlite")
    try:
        store = WorkflowStore(db)
        first = await store.save_draft(workflow_id=None, name="initial", graph=graph())

        async def save(device):
            changed = graph()
            changed.nodes[0].assigned_node_id = device
            return await store.save_draft(
                workflow_id=first.workflow_id,
                name=device,
                graph=changed,
                base_updated_ts=first.updated_ts,
            )

        results = await asyncio.gather(save("one"), save("two"), return_exceptions=True)
        assert sum(isinstance(r, WorkflowConflict) for r in results) == 1
        saved = await store.get_draft(first.workflow_id)
        assert saved.name == saved.graph.nodes[0].assigned_node_id
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_first_lazy_migration_failure_preserves_legacy_blob(tmp_path):
    db = await open_database(tmp_path / "legacy-failure.sqlite")
    try:
        store = WorkflowStore(db)
        raw = graph().model_dump_json()

        async def seed(conn):
            await conn.execute("INSERT INTO workflows VALUES ('w', 'legacy', ?, 1, 1)", (raw,))
            await conn.execute(
                "CREATE TRIGGER fail_first_binding BEFORE INSERT ON workflow_bindings BEGIN SELECT RAISE(ABORT, 'migration failed'); END"
            )

        await db.write(seed)
        with pytest.raises(Exception, match="migration failed"):
            await store.save_draft(
                workflow_id="w", name="changed", graph=graph(), base_updated_ts=1
            )
        async with db.read() as conn:
            row = await (
                await conn.execute(
                    "SELECT name,draft_json,updated_ts FROM workflows WHERE workflow_id='w'"
                )
            ).fetchone()
            assert tuple(row) == ("legacy", raw, 1)
            assert (
                await (await conn.execute("SELECT count(*) FROM workflow_bindings")).fetchone()
            )[0] == 0
        assert (await store.get_draft("w")).graph.nodes[0].assigned_node_id == "device"
    finally:
        await db.close()
