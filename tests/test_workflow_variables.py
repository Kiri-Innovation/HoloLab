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


def test_variable_edit_drives_analysis_dispatch_and_frozen_snapshot(tmp_path, monkeypatch):
    from copy import deepcopy

    from fastapi.testclient import TestClient

    from hololab.gateway.app import create_app
    from hololab.gateway.execution import _dispatch_job, dispatch_graph_node
    from hololab.gateway.preparation import analyze_execution, prepare_execution
    from hololab.gateway.workflows import GraphEdge
    from hololab.manifest.path_params import resolve_path_params
    from hololab.manifest.schema import ParamSpec
    from tests.test_frozen_execution import setup

    async def prepare_paths(app, session, kind, request, *args, **kwargs):
        assert kind == "prepare_params_req"
        return {
            "params": resolve_path_params(
                {"path": ParamSpec(type="path", must_exist=False)},
                request.params,
                check_exists=False,
            )
        }

    monkeypatch.setattr("hololab.gateway.app._await_node_reply", prepare_paths)
    with TestClient(create_app(db_path=tmp_path / "chain.sqlite")) as client:
        app, catalog, graph, received = setup(client, tmp_path)
        catalog[0]["params"]["path"] = {"type": "path", "default": "/default"}
        catalog[0]["inputs"] = {"in": {"tags": ["text"], "required": False}}
        catalog[0]["outputs"] = {"out": {"tags": ["text"]}}
        graph.nodes.append(graph.nodes[0].model_copy(update={"id": "b"}, deep=True))
        graph.edges = [
            GraphEdge(id="ab", source="a", sourceHandle="out", target="b", targetHandle="in")
        ]
        graph.variables = WorkflowGraph(
            variables={"dataset_dir": {"type": "path", "default": ""}}
        ).variables
        graph.variable_values = {"dataset_dir": str(tmp_path / "first")}
        graph.nodes[0].param_bindings = {"path": "dataset_dir"}
        graph.nodes[0].params = {"path": "/original-inline"}

        async def exercise():
            row = await app.state.workflows.save_draft(
                workflow_id=None, name="variables", graph=graph
            )
            frozen = await prepare_execution(app, graph)
            snapshot = await app.state.workflows.create_snapshot(
                workflow_id=row.workflow_id, graph=frozen
            )
            for node in frozen.nodes:
                await _dispatch_job(
                    registry=app.state.registry,
                    store=app.state.jobs_store,
                    hub=app.state.hub,
                    snapshot_id=snapshot.snapshot_id,
                    workflow_id=row.workflow_id,
                    gnode=node,
                    input_handles={},
                    snapshot_jobs=app.state.snapshot_jobs,
                )
            before = (
                await app.state.workflows.get_snapshot(snapshot.snapshot_id)
            ).graph.model_dump_json()
            graph.variable_values["dataset_dir"] = str(tmp_path / "second")
            saved = await app.state.workflows.save_draft(
                workflow_id=row.workflow_id, name="variables", graph=graph
            )
            analysis = await analyze_execution(app, row.workflow_id, saved.graph)
            assert analysis["staleness"]["a"]["kind"] == "self_dirty"
            assert analysis["staleness"]["b"]["kind"] == "upstream_dirty"
            effective = analysis["graph"]["nodes"][0]["params"]
            assert effective["path"] == str(tmp_path / "second")
            outcome = await dispatch_graph_node(
                app,
                workflow_id=row.workflow_id,
                graph=saved.graph,
                graph_node_id="a",
                base_snapshot_id=snapshot.snapshot_id,
            )
            assert outcome["operation"] == "fork"
            assert received[-1][0] == received[-1][1] == effective
            assert (
                await app.state.workflows.get_snapshot(snapshot.snapshot_id)
            ).graph.model_dump_json() == before
            # Restoring the frozen graph also retains its original inline-mode backup.
            restored = await app.state.workflows.save_draft(
                workflow_id=None, name="restore", graph=deepcopy(frozen)
            )
            assert restored.graph.nodes[0].params["path"] == "/original-inline"
            return snapshot.snapshot_id, outcome["snapshot_id"]

        old, new = client.portal.call(exercise)
        history = client.get(f"/api/snapshots/{old}").json()
        assert history["variable_evidence"]["resolved_values"]["dataset_dir"] == str(
            tmp_path / "first"
        )
        assert history["code_evidence"]["a"]["effective_params"]["path"] == str(tmp_path / "first")
        assert client.get(f"/api/snapshots/{new}").json()["variable_evidence"]["resolved_values"][
            "dataset_dir"
        ] == str(tmp_path / "second")
