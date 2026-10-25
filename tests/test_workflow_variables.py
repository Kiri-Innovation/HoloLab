import json

import pytest

from hololab.gateway.preparation import context_revision, resolve_recipe
from hololab.gateway.recipe import compose_graph, split_graph
from hololab.gateway.workflows import GraphNode, WorkflowGraph, WorkflowStore
from hololab.persistence.db import open_database

CATALOG = [
    {
        "name": "stg-train",
        "version": "0.4.6",
        "params": {
            "configpath": {"type": "path", "default": "inline.json"},
            "iterations": {"type": "int", "default": 10},
        },
    }
]


def workflow():
    return WorkflowGraph(
        variables={
            "dataset_dir": {"type": "path", "default": ""},
            "iterations": {"type": "int", "default": 10000},
        },
        variable_values={"dataset_dir": "/datasets/first"},
        nodes=[
            GraphNode(
                id="stg",
                algorithm_name="stg-train",
                algorithm_version="0.4.6",
                assigned_node_id="local",
                params={"configpath": "inline.json"},
                param_bindings={"configpath": "dataset_dir", "iterations": "iterations"},
            )
        ],
    )


def test_resolve_only_authority_preserves_inline_and_records_values():
    graph = workflow()
    resolved = resolve_recipe(graph, CATALOG)
    assert resolved.nodes[0].params == {"configpath": "/datasets/first", "iterations": 10000}
    assert graph.nodes[0].params == {"configpath": "inline.json"}
    assert resolved.execution["sources"]["stg"]["configpath"] == "workflow-variable"
    assert resolved.execution["resolved_variables"]["iterations"] == 10000
    graph.nodes[0].param_bindings.clear()
    assert resolve_recipe(graph, CATALOG).nodes[0].params["configpath"] == "inline.json"


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda g: g.variables.pop("dataset_dir"), "dataset_dir.*未声明"),
        (lambda g: g.variable_values.clear(), "dataset_dir.*未提供值"),
        (
            lambda g: g.nodes[0].param_bindings.update(configpath="iterations"),
            "iterations.*类型不匹配",
        ),
        (lambda g: g.variable_values.update(iterations=True), "iterations.*值类型错误"),
        (lambda g: g.variable_values.update(iterations="100"), "iterations.*值类型错误"),
    ],
)
def test_missing_and_invalid_bindings(change, message):
    graph = workflow()
    change(graph)
    with pytest.raises(ValueError, match=message):
        resolve_recipe(graph, CATALOG)


@pytest.mark.asyncio
async def test_recipe_and_local_bindings_roundtrip(tmp_path):
    graph = workflow()
    recipe, local, view = split_graph(graph)
    assert recipe.graph.variable_values == {}
    assert recipe.graph.variables == graph.variables
    assert compose_graph(recipe, local, view) == graph
    before = context_revision(graph, CATALOG)
    graph.variable_values["dataset_dir"] = "/datasets/second"
    assert context_revision(graph, CATALOG) != before
    db = await open_database(tmp_path / "variables.sqlite")
    try:
        store = WorkflowStore(db)
        row = await store.save_draft(workflow_id=None, name="vars", graph=graph)
        assert row.graph == graph
        async with db.read() as conn:
            draft = json.loads(
                (await (await conn.execute("SELECT draft_json FROM workflows")).fetchone())[0]
            )
            bindings = json.loads(
                (
                    await (
                        await conn.execute("SELECT bindings_json FROM workflow_bindings")
                    ).fetchone()
                )[0]
            )
        assert (
            "variable_values" not in draft and draft["variables"]["iterations"]["default"] == 10000
        )
        assert bindings["variable_values"] == graph.variable_values
        assert bindings["devices"] == {"stg": "local"}
    finally:
        await db.close()
