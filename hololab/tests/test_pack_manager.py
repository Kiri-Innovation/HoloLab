"""HTTP registry → gateway endpoint → real node handler → scanned workflow package."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from hololab.gateway.app import create_app
from hololab.gateway.pack_manager import install_on_node, public_error
from hololab.node.config import NodeConfig
from hololab.node.packs import scan_multi_packs
from hololab.node.runtime import NodeRuntime
from hololab.pack_registry import HashMismatch
from tests.test_pack_registry_urls import registry as http_registry
from tests.test_scoped_packages import session

registry = http_registry


def test_endpoint_installs_and_refreshes_catalog(registry, tmp_path, monkeypatch):
    destination = tmp_path / "installed"
    destination.mkdir()
    runtime = NodeRuntime(NodeConfig(pack_dirs=[destination], workspace_root=tmp_path / "work"))
    runtime._ws = object()
    runtime._safe_send = AsyncMock()
    node = session([])
    node.pack_install_v1 = True

    async def reply(app, target, kind, request, req_id, **kwargs):
        assert kind == "pack_install_req"
        await runtime._pack_install_reply(request)
        result = runtime._safe_send.call_args.args[1]
        if runtime._watch_task:
            runtime._watch_task.cancel()
            await asyncio.gather(runtime._watch_task, return_exceptions=True)
        updates = [
            c.args[1] for c in runtime._safe_send.call_args_list if c.args[0] == "packs_updated"
        ]
        if updates:
            node.packs = updates[-1].packs
            assert updates[-1].envs == {}
        return result.model_dump()

    monkeypatch.setattr("hololab.gateway.app._await_node_reply", reply)
    with TestClient(create_app(db_path=tmp_path / "gateway.sqlite")) as client:
        client.app.state.registry._sessions["n"] = node
        body = {
            "spec": "kiri/demo@1.0.0",
            "source": registry["source"],
            "node_id": "n",
            "expected": registry["release"].code_sha256,
        }
        response = client.post("/api/packs/install", json=body)
        assert response.status_code == 200, response.text
        assert response.json()["hash_verified"]
        assert response.json()["destination"] == str(destination)
        assert scan_multi_packs([destination])[0].manifest.name == "demo"
        catalog = client.get("/api/pack-catalog").json()
        demo = next(pack for pack in catalog if pack["name"] == "demo")
        assert demo["content"]["files"] > 0
        assert demo["content"]["bytes"] > 0
        assert Path(demo["content"]["root_abs"]).is_dir()
        assert Path(demo["content"]["root_abs"]).is_relative_to(destination)
        # Modern nodes describe what is installed; older nodes can still send
        # only the three required fields above.
        assert demo["content"]["hot"]["files"] > 0
        assert isinstance(demo["content"]["cold"]["directories"], list)
        assert isinstance(demo["content"]["excluded"]["top_level"], list)
        assert isinstance(demo["content"]["top_level"], list)
        check = client.post(
            "/api/recipes/check",
            json={
                "graph": {
                    "nodes": [
                        {
                            "id": "demo",
                            "algorithm_name": "kiri/demo",
                            "algorithm_version": "1.0.0",
                            "assigned_node_id": "n",
                        }
                    ],
                    "edges": [],
                }
            },
        )
        assert check.status_code == 200, check.text
        assert not any(i.get("code") == "missing_package" for i in check.json()["issues"])
        registry["release"].yanked = True
        rejected = client.post("/api/packs/install", json=body)
        assert rejected.status_code == 409
        assert rejected.json()["detail"]["code"] == "yanked"
        assert (
            client.get(
                "/api/packs/registry", params={"source": registry["source"], "name": "kiri/demo"}
            ).status_code
            == 200
        )
        assert (
            client.get(
                "/api/packs/registry",
                params={"source": registry["source"], "name": "other/missing"},
            ).json()["detail"]["code"]
            == "not_found"
        )


def test_install_failure_gates(registry, tmp_path):
    destination = tmp_path / "installed"
    destination.mkdir()
    result = install_on_node("kiri/demo@1.0.0", registry["source"], [destination], "0" * 64)
    assert result["error"]["code"] == "hash_mismatch"
    assert not list(destination.iterdir())
    registry["body"] = b"broken"
    result = install_on_node("kiri/demo@1.0.0", registry["source"], [destination], None)
    assert result["error"]["code"] == "hash_mismatch"
    assert not scan_multi_packs([destination])
    assert (
        install_on_node("kiri/demo@1.0.0", registry["source"], [tmp_path / "missing.yaml"], None)[
            "error"
        ]["code"]
        == "no_destination"
    )
    assert public_error(PermissionError("secret path"))["code"] == "disk_error"
    assert "secret" not in str(public_error(HashMismatch("secret hash")))


def test_invalid_request_and_legacy_node(tmp_path):
    with TestClient(create_app(db_path=tmp_path / "gateway.sqlite")) as client:
        assert client.post("/api/packs/install", json={"spec": None}).status_code == 422
        response = client.post(
            "/api/packs/install",
            json={
                "spec": "kiri/demo@1.0.0",
                "source": "https://example.test",
                "node_id": "offline",
            },
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "node_unavailable"
        assert (
            client.get("/api/packs/registry", params={"source": "file:///etc"}).status_code == 502
        )


@pytest.mark.asyncio
async def test_real_websocket_install(registry, tmp_path):
    import socket

    import httpx
    import uvicorn

    root = tmp_path / "packs"
    root.mkdir()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(create_app(db_path=tmp_path / "socket.sqlite"), log_level="error")
    )
    server_task = asyncio.create_task(server.serve(sockets=[sock]))
    runtime = NodeRuntime(
        NodeConfig(
            pack_dirs=[root],
            gateway_url=f"ws://127.0.0.1:{port}",
            workspace_root=tmp_path / "work",
            envs={},
        ),
        config_path=tmp_path / "node.yaml",
    )
    node_task = None
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.05)
        node_task = asyncio.create_task(runtime._connect_loop())
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30) as client:
            nodes = []
            for _ in range(100):
                nodes = (await client.get("/api/nodes")).json()
                if nodes:
                    break
                await asyncio.sleep(0.1)
            assert nodes
            response = await client.post(
                "/api/packs/install",
                json={
                    "node_id": nodes[0]["node_id"],
                    "spec": "kiri/demo@1.0.0",
                    "source": registry["source"],
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["hash_verified"]
            catalog = (await client.get("/api/pack-catalog")).json()
            assert any(pack["name"] == "demo" for pack in catalog)
            demo = next(pack for pack in catalog if pack["name"] == "demo")
            assert demo["publication"]["code_sha256"] == registry["release"].code_sha256
    finally:
        for task in [node_task, runtime._watch_task]:
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        server.should_exit = True
        await server_task
        sock.close()


def test_destination_matches_scanner_modes(registry, tmp_path):
    repository = tmp_path / "repo"
    container = repository / "packs"
    container.mkdir(parents=True)
    single = tmp_path / "single"
    single.mkdir()
    (single / "manifest.yaml").write_text("placeholder")
    result = install_on_node("kiri/demo@1.0.0", registry["source"], [single, repository], None)
    assert result["destination"] == str(container)
    assert len(scan_multi_packs([repository])) == 1
    new = tmp_path / "new-pack-directory"
    result = install_on_node("kiri/demo@1.0.0", registry["source"], [new], None)
    assert result["installed"]
    assert len(scan_multi_packs([new])) == 1
