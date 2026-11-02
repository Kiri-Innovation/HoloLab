from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from hololab.gateway.app import create_app
from hololab.node.config import NodeConfig
from hololab.node.runtime import NodeRuntime
from tests.test_pack_dirs import _write_manifest


def test_manual_reload_without_watcher_updates_catalog(tmp_path, monkeypatch):
    from tests.test_scoped_packages import session

    root = tmp_path / "packs"
    runtime = NodeRuntime(NodeConfig(pack_dirs=[root], install_dir=tmp_path / "installed"))
    runtime._refresh_environment_evidence = AsyncMock()
    node = session([])
    node.node_id = "n"
    replies = []

    async def reply(app, target, kind, request, req_id, **kwargs):
        assert kind == "pack_reload_req"

        async def send(kind, payload):
            if kind == "packs_updated":
                node.packs = payload.packs
                node.envs = payload.envs
            elif kind == "pack_reload_resp":
                replies.append(payload.model_dump())

        runtime._send = send
        await runtime._pack_reload_reply(request)
        return replies[-1]

    monkeypatch.setattr("hololab.gateway.app._await_node_reply", reply)
    with TestClient(create_app(db_path=tmp_path / "gateway.sqlite")) as client:
        client.app.state.registry._sessions["n"] = node
        _write_manifest(root / "manual@0.0.1", "manual", "0.0.1")
        assert runtime._watch_task is None
        assert client.post("/api/packs/reload?node_id=n").json()["nodes"][0]["manifest_changed"]
        catalog = client.get("/api/pack-catalog").json()
        assert any(pack["name"] == "manual" for pack in catalog)
        assert not client.post("/api/packs/reload?node_id=n").json()["nodes"][0]["manifest_changed"]
        client.app.state.registry._sessions.clear()


def test_reload_all_targeted_and_offline(tmp_path, monkeypatch):
    reply = AsyncMock(return_value={"result": {"count": 3, "manifest_changed": True}})
    monkeypatch.setattr("hololab.gateway.app._await_node_reply", reply)
    with TestClient(create_app(db_path=tmp_path / "gateway.sqlite")) as client:
        registry = client.app.state.registry
        registry._sessions = {key: SimpleNamespace(node_id=key) for key in ["a", "b"]}
        response = client.post("/api/packs/reload")
        assert response.status_code == 200
        assert [node["node_id"] for node in response.json()["nodes"]] == ["a", "b"]
        assert reply.await_count == 2
        reply.reset_mock()
        assert client.post("/api/packs/reload?node_id=a").json()["nodes"] == [
            {"node_id": "a", "count": 3, "manifest_changed": True}
        ]
        assert reply.await_count == 1
        assert client.post("/api/packs/reload?node_id=offline").status_code == 404
        registry._sessions.clear()
        assert client.post("/api/packs/reload").json() == {"nodes": []}
