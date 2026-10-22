import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hololab.constants import validate_payload
from hololab.gateway.constants import ensure_inline_inputs
from hololab.gateway.handles import HandleBook
from hololab.gateway.workflows import (
    GraphEdge,
    GraphNode,
    WorkflowGraph,
    WorkflowStore,
    packs_by_key_from_catalog,
    validate_snapshot,
)
from hololab.inline_values import effective_inline, literal_value
from hololab.manifest.schema import InputSpec
from hololab.node.constants import materialize
from hololab.persistence.db import Database
from hololab.protocol import decode
from hololab.protocol.messages import EnsureConstantResp


def catalog(default=None):
    return [
        {
            "name": "consume",
            "version": "1",
            "inputs": {
                "value": {
                    "tags": ["float"],
                    "storage": "file",
                    "required": True,
                    "inline_default": default,
                }
            },
            "outputs": {},
        }
    ]


def graph(values=None):
    node = GraphNode(
        id="task",
        algorithm_name="consume",
        algorithm_version="1",
        assigned_node_id="a",
        port_values=values or {},
    )
    return WorkflowGraph(nodes=[node], edges=[])


@pytest.mark.parametrize(
    "tag,value,axes,storage",
    [
        ("float", -2, [], "file"),
        ("vec3", [0, -1, 0], [], "file"),
        ("float", [], ["layer"], "dir"),
        ("float", [0, -1], ["layer"], "dir"),
        ("vec3", [[0, 0, 1], [0.727, 0.0198, 0.25]], ["layer"], "dir"),
        ("vec3", [[[0, 0, 1]], []], ["item", "layer"], "dir"),
    ],
)
def test_contract_materialization(tmp_path, tag, value, axes, storage):
    digest, payload = literal_value(tag, value, axes)
    assert validate_payload(payload) == digest
    path = Path(materialize(tmp_path, digest, payload))
    assert path.is_file() if storage == "file" else path.is_dir()
    if storage == "file":
        assert json.loads(path.read_text()) == value
        path.unlink()
        assert materialize(tmp_path, digest, payload) == str(path)
        assert json.loads(path.read_text()) == value
    assert materialize(tmp_path, digest, payload) == str(path)


@pytest.mark.parametrize(
    "tag,value,axes",
    [
        ("float", True, []),
        ("float", "2", []),
        ("float", float("nan"), []),
        ("vec3", [0, 1], []),
        ("vec3", [0, 1, float("inf")], []),
        ("float", [0, None], ["layer"]),
        ("video-source", "/tmp/video", []),
    ],
)
def test_format_errors(tag, value, axes):
    with pytest.raises(ValueError):
        literal_value(tag, value, axes)


def test_schema_and_required_and_precedence():
    with pytest.raises(ValueError):
        InputSpec(tags=["image"], storage="file", inline=True)
    with pytest.raises(ValueError):
        InputSpec(tags=["float"], storage="file", inline_default="2")
    packs = packs_by_key_from_catalog(catalog(2))
    g = graph({"value": 0})
    node = g.nodes[0]
    assert effective_inline(node, g, packs[("consume", "1")])["value"][1]["value"] == 0
    node.port_values["value"] = None
    assert effective_inline(node, g, packs[("consume", "1")])["value"][1]["value"] == 2
    source = GraphNode(
        id="source", kind="intrinsic", operation="constant-float-array", params={"items": [1]}
    )
    g.nodes.append(source)
    g.edges.append(
        GraphEdge(
            id="wire", source="source", sourceHandle="offsets", target="task", targetHandle="value"
        )
    )
    node.port_values["value"] = "invalid backup"
    assert effective_inline(node, g, packs[("consume", "1")]) == {}
    g = graph()
    issues = validate_snapshot(
        g,
        packs_by_key=packs_by_key_from_catalog(catalog()),
        online_node_ids={"a"},
        packs_offered_by_node={"a": {("consume", "1")}},
    )
    assert any("'value'" in i.message and "required" in i.message for i in issues)
    g.nodes[0].port_values = {"value": -1}
    assert not validate_snapshot(
        g,
        packs_by_key=packs_by_key_from_catalog(catalog()),
        online_node_ids={"a"},
        packs_offered_by_node={"a": {("consume", "1")}},
    )


@pytest.mark.asyncio
async def test_snapshot_defaults_history_devices_and_no_jobs(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    await db.open()
    try:
        store = WorkflowStore(db)
        g = graph()
        await store.save_draft(workflow_id="w", name="w", graph=g)
        snap = await store.create_snapshot(workflow_id="w", graph=g, catalog=catalog(-2))
        assert snap.graph.nodes[0].port_values == {"value": -2.0}
        assert g.nodes[0].port_values == {}
        # History path copies bindings, even after catalog/draft values changed.
        g.nodes[0].port_values = {"value": 99}
        old = await store.get_snapshot(snap.snapshot_id)
        history = await store.create_snapshot(
            workflow_id="w", graph=old.graph, parent_snapshot_id=snap.snapshot_id
        )
        app = SimpleNamespace(state=SimpleNamespace(db=db, handles=HandleBook(db)))
        sessions = {}
        for device in ("a", "b"):
            session = SimpleNamespace(
                node_id=device, intrinsics=["literal-v3"], send_lock=asyncio.Lock(), protocol_v=1
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
        for sid in (snap.snapshot_id, history.snapshot_id):
            for device in ("a", "b"):
                consumer = old.graph.nodes[0].model_copy(update={"assigned_node_id": device})
                results = await asyncio.gather(
                    *[ensure_inline_inputs(app, sid, consumer, {}) for _ in range(3)]
                )
                assert results[0] == results[1] == results[2]
                handle = await app.state.handles.get(results[0]["value"])
                assert handle.job_id is None and handle.storage == "file"
                assert json.loads(Path(handle.path).read_text()) == -2
                Path(handle.path).parent.rename(
                    Path(handle.path).parent.with_name("removed-" + device + "-" + sid)
                )
                await ensure_inline_inputs(app, sid, consumer, {})
                assert Path(handle.path).exists()
        assert len(await app.state.handles.list_by_snapshot(snap.snapshot_id)) == 2
        # A wire always wins, even if its handle is unusable: no inline fallback.
        assert await ensure_inline_inputs(
            app, snap.snapshot_id, old.graph.nodes[0], {"value": "wire"}
        ) == {"value": "wire"}
        legacy = await store.create_snapshot(workflow_id="w", graph=graph())
        assert await ensure_inline_inputs(app, legacy.snapshot_id, legacy.graph.nodes[0], {}) == {}
        async with db.read() as conn, conn.execute("SELECT count(*) FROM jobs") as cur:
            assert (await cur.fetchone())[0] == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_failed_wire_never_uses_backup_literal():
    from unittest.mock import AsyncMock

    from hololab.gateway.execution import DispatchError, _resolve_inputs_from_snapshot
    from hololab.gateway.jobs import JobState

    g = graph({"value": -1})
    g.nodes.append(GraphNode(id="source", algorithm_name="produce", algorithm_version="1"))
    g.edges.append(
        GraphEdge(id="e", source="source", sourceHandle="out", target="task", targetHandle="value")
    )
    app = SimpleNamespace(
        state=SimpleNamespace(
            snapshot_jobs=SimpleNamespace(get_job_at=AsyncMock(return_value="failed-job")),
            jobs_store=SimpleNamespace(
                get=AsyncMock(return_value=SimpleNamespace(state=JobState.FAILED))
            ),
            handles=SimpleNamespace(list_by_job=AsyncMock()),
        )
    )
    with pytest.raises(DispatchError, match="failed"):
        await _resolve_inputs_from_snapshot(
            app, workflow_id="w", target_snapshot_id="s", graph=g, graph_node_id="task"
        )
    app.state.handles.list_by_job.assert_not_awaited()


def test_workflow_api_preserves_inline_values(tmp_path):
    from fastapi.testclient import TestClient

    from hololab.gateway.app import create_app

    with TestClient(create_app(db_path=tmp_path / "api.sqlite")) as client:
        response = client.post(
            "/api/workflows", json={"name": "inline", "graph": graph({"value": 0}).model_dump()}
        )
        assert response.status_code == 200, response.text
        wid = response.json()["workflow_id"]
        saved = client.get("/api/workflows/" + wid).json()
        assert saved["graph"]["nodes"][0]["port_values"] == {"value": 0}
        assert (
            client.post(
                "/api/workflows",
                json={
                    "workflow_id": wid,
                    "name": "inline",
                    "graph": saved["graph"],
                    "base_updated_ts": saved["updated_ts"],
                },
            ).status_code
            == 200
        )
        assert client.get("/api/workflows/" + wid).json()["graph"]["nodes"][0]["port_values"] == {
            "value": 0
        }
