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
