import json
import shutil
from pathlib import Path

import pytest

from hololab.constants import constant_value
from hololab.gateway.workflows import (
    GraphNode,
    WorkflowGraph,
    execution_topological_order,
    validate_snapshot,
)
from hololab.node.constants import materialize


def test_constant_contract_and_cache(tmp_path):
    digest, value = constant_value("constant-float-array", {"items": [0, 2.5]})
    assert digest == constant_value("constant-float-array", {"items": [0.0, 2.5]})[0]
    assert digest != constant_value("constant-float-array", {"items": [2.5, 0]})[0]
    out = Path(materialize(tmp_path, digest, value))
    stamp = out.stat().st_mtime_ns
    assert materialize(tmp_path, digest, value) == str(out)
    assert out.stat().st_mtime_ns == stamp
    assert json.loads((out / "0001").read_text()) == 2.5
    shutil.rmtree(out)
    assert Path(materialize(tmp_path, digest, value)).is_dir()
    other = tmp_path / "other-device"
    assert Path(materialize(other, digest, value)).is_dir()
    with pytest.raises(ValueError, match="digest"):
        materialize(tmp_path, "wrong", value)
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")
    with pytest.raises(OSError):
        materialize(blocked, digest, value)


@pytest.mark.parametrize("value", [True, "2", float("nan"), float("inf"), -1])
def test_invalid_constant(value):
    with pytest.raises(ValueError):
        constant_value("constant-float-array", {"items": [value]})


def test_constant_has_no_task_or_device_requirement():
    node = GraphNode(
        id="constant", kind="intrinsic", operation="constant-float-array", params={"items": [0]}
    )
    graph = WorkflowGraph(nodes=[node], edges=[])
    assert execution_topological_order(graph) == []
    assert (
        validate_snapshot(graph, packs_by_key={}, online_node_ids=set(), packs_offered_by_node={})
        == []
    )
    assert node.algorithm_name == "@builtin/constant-float-array"


@pytest.mark.asyncio
async def test_snapshot_binding_and_device_materialization(tmp_path):
    import asyncio
    from types import SimpleNamespace

    from hololab.gateway.constants import ensure_input
    from hololab.gateway.handles import HandleBook
    from hololab.gateway.workflows import WorkflowStore
    from hololab.persistence.db import Database
    from hololab.protocol import decode
    from hololab.protocol.messages import EnsureConstantResp

    db = Database(tmp_path / "test.sqlite")
    await db.open()
    try:
        store = WorkflowStore(db)
        node = GraphNode(
            id="c", kind="intrinsic", operation="constant-float-array", params={"items": [0, 2.5]}
        )
        graph = WorkflowGraph(nodes=[node], edges=[])
        # Snapshot creation freezes values even without a consumer or device.
        await store.save_draft(workflow_id="test", name="test", graph=graph)
        snap = await store.create_snapshot(workflow_id="test", graph=graph)
        app = SimpleNamespace(state=SimpleNamespace(db=db, handles=HandleBook(db)))
        sessions = {}
        for device in ["a", "b"]:
            session = SimpleNamespace(
                node_id=device, intrinsics=["constant-v1"], send_lock=asyncio.Lock(), protocol_v=1
            )

            async def send(frame, session=session):
                _, req = decode(frame)
                path = materialize(tmp_path / session.node_id, req.digest, req.value)
                app.state.pending_constants[req.req_id][1].set_result(
                    EnsureConstantResp(req_id=req.req_id, digest=req.digest, path=path)
                )

            session.ws = SimpleNamespace(send_text=send)
            sessions[device] = session
        app.state.registry = SimpleNamespace(get_session=sessions.get)
        consumer = SimpleNamespace(assigned_node_id="a")
        handles = await asyncio.gather(
            *[ensure_input(app, snap.snapshot_id, node, consumer) for _ in range(5)]
        )
        assert len(set(handles)) == 1
        handle = await app.state.handles.get(handles[0])
        assert handle.job_id is None
        assert len(await app.state.handles.list_by_snapshot(snap.snapshot_id)) == 1
        shutil.rmtree(handle.path)
        assert await ensure_input(app, snap.snapshot_id, node, consumer) == handles[0]
        consumer.assigned_node_id = "b"
        second = await ensure_input(app, snap.snapshot_id, node, consumer)
        assert second != handles[0]
        assert len(await app.state.handles.list_by_snapshot(snap.snapshot_id)) == 2
        # Changing a draft cannot overwrite an existing snapshot's frozen binding.
        changed = node.model_copy(update={"params": {"items": [9]}})
        with pytest.raises(ValueError, match="frozen"):
            await ensure_input(app, snap.snapshot_id, changed, consumer)

        async def failed(frame):
            _, req = decode(frame)
            app.state.pending_constants[req.req_id][1].set_result(
                EnsureConstantResp(req_id=req.req_id, digest=req.digest, error="disk full")
            )

        # Lost acknowledgements retry with the same content/handle identity.
        original_send = sessions["a"].ws.send_text
        attempts = 0

        async def flaky(frame):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise TimeoutError("lost acknowledgement")
            await original_send(frame)

        sessions["a"].ws.send_text = flaky
        consumer.assigned_node_id = "a"
        assert await ensure_input(app, snap.snapshot_id, node, consumer) == handles[0]
        assert attempts == 2
        consumer.assigned_node_id = "b"

        sessions["b"].ws.send_text = failed
        with pytest.raises(ValueError, match="disk full"):
            await ensure_input(app, snap.snapshot_id, node, consumer)
        # Shared cache remains protected after one workflow is deleted.
        await store.save_draft(workflow_id="other", name="other", graph=graph)
        await store.create_snapshot(workflow_id="other", graph=graph)
        await store.delete_draft("test")
        from fastapi import HTTPException

        from hololab.gateway.artifacts import delete_artifact

        with pytest.raises(HTTPException) as error:
            await delete_artifact(app, sessions["a"], handle)
        assert error.value.status_code == 409
        assert Path(handle.path).is_dir()
        async with db.read() as conn, conn.execute("SELECT COUNT(*) FROM jobs") as cur:
            assert (await cur.fetchone())[0] == 0
    finally:
        await db.close()


def test_constant_api_save_without_devices_and_no_empty_run(tmp_path):
    from fastapi.testclient import TestClient

    from hololab.gateway.app import create_app

    app = create_app(db_path=tmp_path / "api.sqlite")
    with TestClient(app) as client:
        graph = {
            "nodes": [
                {
                    "id": "c",
                    "kind": "intrinsic",
                    "operation": "constant-float-array",
                    "params": {"items": [0, 2]},
                }
            ],
            "edges": [],
        }
        saved = client.post("/api/workflows", json={"name": "constant-test", "graph": graph})
        assert saved.status_code == 200, saved.text
        wid = saved.json()["workflow_id"]
        result = client.post(f"/api/workflows/{wid}/run", json={})
        assert result.status_code == 200, result.text
        assert result.json()["snapshot_id"] is None
        assert result.json()["node_count"] == 0
        rejected = client.post(f"/api/workflows/{wid}/run", json={"node_id": "c"})
        assert rejected.status_code in (400, 422), rejected.text
        assert "常量无需运行" in rejected.text
        got = client.get(f"/api/workflows/{wid}").json()
        assert got["graph"]["nodes"][0]["constant"]["status"] == "ready"

        async def counts():
            async with app.state.db.read() as conn:
                async with conn.execute("SELECT COUNT(*) FROM jobs") as cur:
                    jobs = (await cur.fetchone())[0]
                async with conn.execute("SELECT COUNT(*) FROM snapshots") as cur:
                    snapshots = (await cur.fetchone())[0]
            return jobs, snapshots

        assert client.portal.call(counts) == (0, 0)
