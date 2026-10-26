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


@pytest.mark.asyncio
async def test_satisfied_contract_runs_real_process(tmp_path, monkeypatch):
    import yaml

    from hololab.node import runtime

    _, path, _ = package(tmp_path)
    data = yaml.safe_load(path.read_text())
    data.update(
        requires={"python": ">=3"},
        exec={
            "shell": 'mkdir -p "{{ outputs.out }}"; printf ENV_CONTRACT_OK > "{{ outputs.out }}/result.txt"'
        },
    )
    path.write_text(yaml.safe_dump(data))
    pack = scan_packs(path)[0]
    node = NodeRuntime(NodeConfig(workspace_root=tmp_path / "ws", envs={"test": sys.prefix}))
    node._node_id = "test-node"
    node._packs = {(pack.manifest.name, pack.manifest.version): pack}
    node._env_cache[sys.prefix] = dict(os.environ)
    node._send = AsyncMock()
    node._safe_send = AsyncMock()
    node._send_job_fail = AsyncMock()
    executor = AsyncMock(wraps=runtime.run_subprocess)
    monkeypatch.setattr(runtime, "run_subprocess", executor)
    await node._run_job(
        JobAssign(
            job_id="satisfied", workflow_id="w", algorithm_name="demo", algorithm_version="1.0.0"
        ),
        asyncio.Event(),
    )
    executor.assert_awaited_once()
    node._send_job_fail.assert_not_called()
    assert any(call.args[0] == "job_done" for call in node._send.call_args_list)


def test_old_node_cannot_accept_recorded_contract():
    from types import SimpleNamespace

    from hololab.gateway.capabilities import require_check_support

    node = GraphNode(
        id="a",
        algorithm_name="demo",
        algorithm_version="1.0.0",
        code_identity={"manifest": {"normalized": {"requires": {"python": ">=3"}}}},
    )
    with pytest.raises(ValueError, match="升级节点"):
        require_check_support(SimpleNamespace(packs=[]), node)


def test_api_distinguishes_explicit_empty_contract_from_undeclared(tmp_path, monkeypatch):
    _, path, _ = package(tmp_path)
    path.write_text(path.read_text() + "\nrequires: {}\n")
    pack = scan_packs(path)[0]
    selected = session([pack])
    selected.environment_checks = True
    selected.packs[0].requires = pack.manifest.requires.as_dict()

    async def rpc(app, selected, kind, request, *args, **kwargs):
        assert kind == "capability_req"
        return {"report": CapabilityChecker().check(pack.manifest, {}, "missing")}

    monkeypatch.setattr("hololab.gateway.app._await_node_reply", rpc)
    with TestClient(create_app(db_path=tmp_path / "test.sqlite")) as client:
        client.app.state.registry._sessions["n"] = selected
        catalog = client.get("/api/pack-catalog").json()
        assert catalog[0]["requires"] == {}
        graph = WorkflowGraph(
            nodes=[
                GraphNode(
                    id="a", algorithm_name="demo", algorithm_version="1.0.0", assigned_node_id="n"
                )
            ]
        )
        response = client.post("/api/capabilities/check", json={"graph": graph.model_dump()})
        assert response.status_code == 200
        assert response.json()["nodes"]["a"] == {
            "status": "satisfied",
            "issues": [],
            "boundary": "本地能力检查只覆盖已声明且在可探测范围内的项; 通过不代表一定能运行。环境未冻结、未被代码身份覆盖。",
            "environment_covered": False,
            "runtime_verified": False,
            "checked_at": None,
            "requires": {},
            "env": "test",
            "node_id": "n",
            "source": "demo@1.0.0",
        }


@pytest.mark.asyncio
async def test_submission_records_environment_without_changing_code_digest(tmp_path):
    from hololab.content_scope import capture
    from hololab.protocol.messages import CodeIdentityReq

    _, path, baselines = package(tmp_path)
    path.write_text(path.read_text() + '\nrequires: {python: ">=3"}\n')
    expected = capture(path, baselines)
    pack = scan_packs(path)[0]
    node = NodeRuntime(
        NodeConfig(
            workspace_root=tmp_path / "ws",
            envs={"test": sys.prefix},
            code_baselines_dir=baselines.directory,
        )
    )
    node._packs = {(pack.manifest.name, pack.manifest.version): pack}
    node._env_cache[sys.prefix] = dict(os.environ)
    node._safe_send = AsyncMock()
    await node._code_identity_reply(
        CodeIdentityReq(req_id="r", algorithm_name="demo", algorithm_version="1.0.0")
    )
    response = node._safe_send.call_args.args[1]
    assert response.error is None
    assert response.record["sha256"] == expected["sha256"]
    assert response.record["environment"]["status"] == "satisfied"
    assert response.record["environment"]["environment_covered"] is False
    assert response.record["environment"]["runtime_verified"] is False


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

        selected.environment_checks = False
        old_node = client.post("/api/capabilities/check", json={"graph": graph.model_dump()}).json()
        assert old_node["nodes"]["a"]["status"] == "unknown"
        assert "升级节点" in old_node["nodes"]["a"]["issues"][0]["actual"]

        graph.nodes[0].algorithm_name = "unavailable-pack"
        unavailable = client.post(
            "/api/capabilities/check", json={"graph": graph.model_dump()}
        ).json()
        assert unavailable["nodes"]["a"]["status"] == "unknown"  # Never invent undeclared.


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
