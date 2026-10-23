import json

import pytest

from hololab.gateway.recipe import Bindings, compose_graph, copy_recipe, split_graph
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
    assert compose_graph(recipe, Bindings({}), view).nodes[0].assigned_node_id is None
    assert copy_recipe(g).nodes[0].params == g.nodes[0].params
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
        copied = await store.copy_recipe("w", "copy")
        assert copied.graph.nodes[0].assigned_node_id is None
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
