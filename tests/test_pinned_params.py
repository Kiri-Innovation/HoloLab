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
