import asyncio
from unittest.mock import AsyncMock

import yaml
from fastapi.testclient import TestClient

from hololab.gateway.app import create_app
from hololab.node.config import NodeConfig
from hololab.node.runtime import NodeRuntime
from tests.test_pack_dirs import _write_manifest
from tests.test_scoped_packages import session


def test_settings_roundtrip_rescan_and_catalog_destination(tmp_path, monkeypatch):
    monkeypatch.setenv("HOLOLAB_HOME", str(tmp_path / "home"))
    path = tmp_path / "config.yaml"
    runtime = NodeRuntime(NodeConfig(pack_dirs=[tmp_path / "dev"]), config_path=path)
    runtime._send = AsyncMock()
    runtime._safe_send = AsyncMock()
    runtime._restart_file_server = AsyncMock()
    runtime._refresh_environment_evidence = AsyncMock()
    runtime._ws = object()
    node = session([])
    target = tmp_path / "downloads"
    _write_manifest(target / "example", "example", "1.0.0")

    async def reply(app, target_node, kind, req, req_id):
        if kind == "node_config_get_req":
            await runtime._handle_config_get_req(req)
        else:
            await runtime._handle_config_set_req(req)
        if runtime._watch_task:
            runtime._watch_task.cancel()
            await asyncio.gather(runtime._watch_task, return_exceptions=True)
            runtime._watch_task = None
        return runtime._send.call_args.args[1].model_dump()

    monkeypatch.setattr("hololab.gateway.app._await_config_reply", reply)
    with TestClient(create_app(db_path=tmp_path / "gateway.sqlite")) as client:
        client.app.state.registry._sessions["n"] = node
        url = "/api/nodes/n/config"
        initial = client.get(url).json()["config"]
        assert initial["install_dir"] is None
        assert initial["default_install_dir"] == str(tmp_path / "home/node/packs")
        response = client.patch(url, json={"patch": {"install_dir": str(target)}})
        assert response.status_code == 200, response.text
        assert yaml.safe_load(path.read_text())["install_dir"] == str(target)
        assert ("example", "1.0.0") in runtime._packs
        assert any(call.args[0] == "packs_updated" for call in runtime._safe_send.call_args_list)
        runtime._restart_file_server.assert_awaited_once()
        assert node.install_destination == str(target)
        assert node.pack_dirs[0] == str(target)
        for invalid in ["relative/path", 42]:
            assert client.patch(url, json={"patch": {"install_dir": invalid}}).status_code == 400
        assert yaml.safe_load(path.read_text())["install_dir"] == str(target)
        cleared = client.patch(url, json={"patch": {"install_dir": ""}})
        assert cleared.status_code == 200
        assert yaml.safe_load(path.read_text())["install_dir"] is None
        assert node.install_destination == initial["default_install_dir"]
        # Other settings still share the same save path.
        changed = client.patch(
            url,
            json={
                "patch": {
                    "workspace_root": str(tmp_path / "artifacts"),
                    "legacy_workspace_roots": [str(tmp_path / "old")],
                    "pack_dirs": [str(tmp_path / "local")],
                }
            },
        )
        assert changed.status_code == 200
        assert changed.json()["config"]["workspace_root"] == str(tmp_path / "artifacts")
