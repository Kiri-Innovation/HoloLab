"""Workflow-wide pins survive public serialization and freeze in snapshots."""

import pytest
from pydantic import ValidationError

from hololab.gateway.models import WorkflowGraphOut
from hololab.gateway.workflows import (
    WorkflowConflict,
    WorkflowGraph,
    WorkflowStore,
    agent_graph_dict,
)
from hololab.persistence.db import open_database


def test_pin_wire_roundtrip_and_old_graph():
    assert WorkflowGraph.model_validate({"nodes": [], "edges": []}).pinned_params == {}
    graph = WorkflowGraph(pinned_params={"demo@1": ["count", "retired_field"]})
    public = WorkflowGraphOut.model_validate(agent_graph_dict(graph)).model_dump()
    assert WorkflowGraph.model_validate(public).pinned_params == graph.pinned_params
    with pytest.raises(ValidationError):
        WorkflowGraph(pinned_params={"demo@1": [123]})


async def test_pin_persistence_snapshot_and_conflict(tmp_path):
    db = await open_database(tmp_path / "pins.sqlite")
    try:
        store = WorkflowStore(db)
        old = WorkflowGraph(pinned_params={"demo@1": ["count"]})
        row = await store.save_draft(workflow_id=None, name="pins", graph=old)
        snap = await store.create_snapshot(workflow_id=row.workflow_id, graph=old)
        new = WorkflowGraph(pinned_params={"demo@1": ["label"], "demo@2": ["count"]})
        await store.save_draft(
            workflow_id=row.workflow_id, name="pins", graph=new, base_updated_ts=row.updated_ts
        )
        assert (await store.get_draft(row.workflow_id)).graph.pinned_params == new.pinned_params
        assert (await store.get_snapshot(snap.snapshot_id)).graph.pinned_params == old.pinned_params
        with pytest.raises(WorkflowConflict):
            await store.save_draft(
                workflow_id=row.workflow_id, name="stale", graph=old, base_updated_ts=row.updated_ts
            )
    finally:
        await db.close()


def test_pin_http_roundtrip_and_snapshot_freeze(tmp_path):
    from fastapi.testclient import TestClient

    from hololab.gateway.app import create_app

    with TestClient(create_app(db_path=tmp_path / "pin-api.sqlite")) as client:
        payload = {
            "name": "pins",
            "graph": {"nodes": [], "edges": [], "pinned_params": {"demo@1": ["count"]}},
        }
        response = client.post("/api/workflows", json=payload)
        assert response.status_code == 200, response.text
        workflow_id = response.json()["workflow_id"]
        current = client.get(f"/api/workflows/{workflow_id}").json()
        assert current["graph"]["pinned_params"] == {"demo@1": ["count"]}

        async def snapshot():
            return await client.app.state.workflows.create_snapshot(
                workflow_id=workflow_id, graph=WorkflowGraph.model_validate(current["graph"])
            )

        snap = client.portal.call(snapshot)
        payload.update(workflow_id=workflow_id, base_updated_ts=current["updated_ts"])
        payload["graph"]["pinned_params"] = {"demo@1": ["label"]}
        assert client.post("/api/workflows", json=payload).status_code == 200
        frozen = client.get(f"/api/snapshots/{snap.snapshot_id}")
        assert frozen.status_code == 200
        assert frozen.json()["graph"]["pinned_params"] == {"demo@1": ["count"]}
        assert client.get(f"/api/workflows/{workflow_id}").json()["graph"]["pinned_params"] == {
            "demo@1": ["label"]
        }


def test_manifest_default_pin_strict_and_unknown_pin_fields():
    from hololab.manifest.schema import ParamSpec

    assert ParamSpec(type="int", pinned=True).pinned is True
    assert ParamSpec(type="int").pinned is False
    for value in ("true", 1, None):
        with pytest.raises(ValidationError):
            ParamSpec(type="int", pinned=value)
    for key in ("pin", "pinned_params", "default_pinned"):
        with pytest.raises(ValidationError, match="Unknown pin field"):
            ParamSpec.model_validate({"type": "int", key: True})


async def test_snapshot_materializes_defaults_without_changing_draft(tmp_path):
    from hololab.gateway.models import ParamSpecOut
    from hololab.gateway.workflows import GraphNode, freeze_parameter_pins

    graph = WorkflowGraph(
        nodes=[GraphNode(id="n", algorithm_name="demo", algorithm_version="1.0.0")]
    )
    catalog = [
        {
            "name": "demo",
            "version": "1.0.0",
            "params": {"count": {"type": "int", "pinned": True}, "label": {"type": "string"}},
        }
    ]
    assert ParamSpecOut.model_validate(catalog[0]["params"]["count"]).pinned
    frozen = freeze_parameter_pins(graph, catalog)
    assert frozen.pinned_params == {"demo@1.0.0": ["count"]}
    assert graph.pinned_params == {}
    explicit = graph.model_copy(update={"pinned_params": {"demo@1.0.0": []}})
    assert freeze_parameter_pins(explicit, catalog).pinned_params == {"demo@1.0.0": []}
    assert freeze_parameter_pins(graph, []).pinned_params == {"demo@1.0.0": []}
    db = await open_database(tmp_path / "defaults.sqlite")
    try:
        store = WorkflowStore(db)
        draft = await store.save_draft(workflow_id=None, name="defaults", graph=graph)
        snap = await store.create_snapshot(
            workflow_id=draft.workflow_id, graph=graph, catalog=catalog
        )
        catalog[0]["params"]["count"]["pinned"] = False
        assert (await store.get_snapshot(snap.snapshot_id)).graph.pinned_params == {
            "demo@1.0.0": ["count"]
        }
        assert (await store.get_draft(draft.workflow_id)).graph.pinned_params == {}
    finally:
        await db.close()


def test_transform_defaults_freeze_as_one_row():
    from hololab.gateway.workflows import GraphNode, freeze_parameter_pins

    graph = WorkflowGraph(
        nodes=[GraphNode(id="n", algorithm_name="vec3-array-source", algorithm_version="0.1.0")]
    )
    params = {
        name: {"type": "array", "items": "float", "pinned": name == "x"}
        for name in ("x", "y", "scale")
    }
    frozen = freeze_parameter_pins(
        graph, [{"name": "vec3-array-source", "version": "0.1.0", "params": params}]
    )
    assert frozen.pinned_params == {"vec3-array-source@0.1.0": ["x", "y", "scale"]}
