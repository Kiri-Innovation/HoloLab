import pytest

from hololab.gateway.jobs import Job
from hololab.gateway.registry import JobsStore
from hololab.persistence.db import open_database


@pytest.mark.asyncio
async def test_identity_survives_reopen_and_cannot_be_replaced(tmp_path):
    path = tmp_path / "state.sqlite"
    db = await open_database(path)
    store = JobsStore(db)
    await store.create(
        Job(
            job_id="j",
            workflow_id="w",
            snapshot_id=None,
            algorithm_name="demo",
            algorithm_version="1.0",
        )
    )
    assert await store.get_code_identity("j") is None  # Old rows stay readable.
    record = {"hot": {"sha256": "original"}, "cold": {"baseline_at": 123}}
    await store.set_code_identity("j", record)
    await store.set_code_identity("j", record)
    with pytest.raises(ValueError, match="cannot be replaced"):
        await store.set_code_identity("j", {"hot": {"sha256": "new"}})
    await db.close()
    db = await open_database(path)
    try:
        assert await JobsStore(db).get_code_identity("j") == record
    finally:
        await db.close()


def test_snapshot_api_exposes_saved_evidence_without_catalog(tmp_path):
    from fastapi.testclient import TestClient

    from hololab.gateway.app import create_app
    from hololab.gateway.workflows import GraphNode, WorkflowGraph

    frozen = {"format": 2, "sha256": "saved-identity", "effective_params": {"steps": 10}}
    with TestClient(create_app(db_path=tmp_path / "api.sqlite")) as client:

        async def seed():
            graph = WorkflowGraph(
                nodes=[
                    GraphNode(
                        id="recorded",
                        algorithm_name="demo",
                        algorithm_version="0.1.1+",
                        code_identity=frozen,
                        params={"steps": 10},
                    ),
                    GraphNode(id="legacy", algorithm_name="demo", algorithm_version="0.1.0"),
                ]
            )
            store = client.app.state.workflows
            draft = await store.save_draft(workflow_id=None, name="frozen", graph=graph)
            return await store.create_snapshot(workflow_id=draft.workflow_id, graph=graph)

        snapshot = client.portal.call(seed)
        response = client.get(f"/api/snapshots/{snapshot.snapshot_id}")
        assert response.status_code == 200, response.text
        evidence = response.json()["code_evidence"]
        assert evidence["recorded"] == {
            "status": "recorded",
            "code_identity": frozen,
            "effective_params": {"steps": 10},
        }
        assert evidence["legacy"] == {
            "status": "unrecorded",
            "code_identity": None,
            "effective_params": {},
        }


@pytest.mark.asyncio
async def test_snapshot_freezes_evidence_and_legacy_is_never_backfilled(tmp_path):
    from types import SimpleNamespace

    from hololab.content_scope import capture
    from hololab.gateway.preparation import historical_execution, snapshot_code_evidence
    from hololab.gateway.workflows import GraphNode, WorkflowGraph, WorkflowStore
    from tests.test_content_scope import package

    root, manifest, baselines = package(tmp_path)
    record = capture(manifest, baselines)
    record["effective_params"] = {"iterations": 10}
    db = await open_database(tmp_path / "snapshots.sqlite")
    try:
        store = WorkflowStore(db)
        graph = WorkflowGraph(
            nodes=[
                GraphNode(
                    id="a",
                    algorithm_name="demo",
                    algorithm_version="1.0.0",
                    code_identity=record,
                    params={"iterations": 10},
                )
            ],
            execution={"version": 1, "prepared_nodes": ["a"]},
        )
        draft = await store.save_draft(workflow_id=None, name="evidence", graph=graph)
        saved = await store.create_snapshot(workflow_id=draft.workflow_id, graph=graph)
        old_graph = graph.model_copy(deep=True)
        old_graph.nodes[0].code_identity = None
        legacy = await store.create_snapshot(workflow_id=draft.workflow_id, graph=old_graph)
        async with db.read() as conn:
            before = await (
                await conn.execute(
                    "SELECT snapshot_id,graph_json FROM snapshots ORDER BY snapshot_id"
                )
            ).fetchall()
        (root / "thirdparty/lib.py").write_text("new cold code")
        updated = capture(manifest, baselines, refresh_cold=True)
        assert updated["sha256"] != record["sha256"]
        frozen = await historical_execution(
            SimpleNamespace(), await store.get_snapshot(saved.snapshot_id)
        )
        assert frozen.nodes[0].code_identity == record
        assert frozen.nodes[0].params == {"iterations": 10}
        evidence = snapshot_code_evidence((await store.get_snapshot(legacy.snapshot_id)).graph)
        assert evidence["a"]["status"] == "unrecorded"
        assert evidence["a"]["code_identity"] is None
        async with db.read() as conn:
            after = await (
                await conn.execute(
                    "SELECT snapshot_id,graph_json FROM snapshots ORDER BY snapshot_id"
                )
            ).fetchall()
        assert before == after
    finally:
        await db.close()
