import asyncio
import os
import sys
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from hololab.gateway.app import create_app
from hololab.gateway.preparation import prepare_execution
from hololab.gateway.workflows import GraphNode, WorkflowGraph
from hololab.node.capabilities import CapabilityChecker
from hololab.node.config import NodeConfig
from hololab.node.packs import scan_packs
from hololab.node.runtime import NodeRuntime
from hololab.protocol.messages import JobAssign
from tests.test_content_scope import package
from tests.test_scoped_packages import session


def make_pack(tmp_path):
    _, path, _ = package(tmp_path)
    path.write_text(
        path.read_text() + "\nrequires:\n  packages: [hololab-certainly-missing-contract-test]\n"
    )
    return scan_packs(path)[0]


def test_workflow_open_lists_missing_and_preparation_fails(tmp_path, monkeypatch):
    pack = make_pack(tmp_path)
    checker = CapabilityChecker()
    envs = {"test": sys.prefix}

    async def rpc(app, selected, kind, request, *args, **kwargs):
        assert kind == "capability_req"
        return {
            "report": checker.check(
                pack.manifest, envs, "unused", dict(os.environ), fresh=request.fresh
            )
        }

    monkeypatch.setattr("hololab.gateway.app._await_node_reply", rpc)
    with TestClient(create_app(db_path=tmp_path / "test.sqlite")) as client:
        selected = session([pack])
        selected.environment_checks = True
        selected.packs[0].requires = pack.manifest.requires.model_dump()
        client.app.state.registry._sessions["n"] = selected
        graph = WorkflowGraph(
            nodes=[
                GraphNode(
                    id="a", algorithm_name="demo", algorithm_version="1.0.0", assigned_node_id="n"
                )
            ]
        )
        response = client.post("/api/capabilities/check", json={"graph": graph.model_dump()})
        assert response.status_code == 200
        report = response.json()["nodes"]["a"]
        assert report["status"] == "missing"
        assert report["issues"][0]["actual"] == "未安装"
        assert "手动安装" in report["issues"][0]["remedy"]

        async def prepare():
            with pytest.raises(ValueError, match=r"hololab-certainly-missing.*未安装"):
                await prepare_execution(client.app, graph)

        client.portal.call(prepare)
        selected.ws.send_text.assert_not_called()  # No JobAssign.


@pytest.mark.asyncio
async def test_node_blocks_missing_contract_before_process_spawn(tmp_path, monkeypatch):
    pack = make_pack(tmp_path)
    node = NodeRuntime(NodeConfig(workspace_root=tmp_path / "ws", envs={"test": sys.prefix}))
    node._packs = {(pack.manifest.name, pack.manifest.version): pack}
    node._env_cache[sys.prefix] = dict(os.environ)
    node._send = AsyncMock()
    node._safe_send = AsyncMock()
    node._send_job_fail = AsyncMock()
    executor = AsyncMock()
    monkeypatch.setattr("hololab.node.runtime.run_subprocess", executor)
    await node._run_job(
        JobAssign(
            job_id="missing-env", workflow_id="w", algorithm_name="demo", algorithm_version="1.0.0"
        ),
        asyncio.Event(),
    )
    executor.assert_not_called()
    assert "未安装" in node._send_job_fail.call_args.kwargs["message"]
