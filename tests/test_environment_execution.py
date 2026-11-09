import asyncio
import os
import sys
from types import SimpleNamespace
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
from hololab.protocol.messages import CapabilityReq, JobAssign
from tests.test_content_scope import package
from tests.test_scoped_packages import session


@pytest.mark.asyncio
async def test_environment_evidence_prefix_is_resolved_once_on_node(tmp_path):
    """Logical env names are never mistaken for their machine-local prefix."""

    node = NodeRuntime(NodeConfig(workspace_root=tmp_path / "ws", envs={"kiri": "/env/kiri"}))
    node._capability_checker = SimpleNamespace(
        check=lambda *_args, **_kwargs: {"status": "satisfied"}
    )
    conda_manifest = SimpleNamespace(runtime=SimpleNamespace(mode="conda", env="kiri"))
    unmapped_manifest = SimpleNamespace(runtime=SimpleNamespace(mode="conda", env="missing"))
    host_manifest = SimpleNamespace(runtime=SimpleNamespace(mode="host", env="kiri"))

    assert (await node._environment_report(conda_manifest))["prefix"] == "/env/kiri"
    assert (await node._environment_report(unmapped_manifest))["prefix"] is None
    assert (await node._environment_report(host_manifest))["prefix"] is None


@pytest.mark.asyncio
async def test_environment_recheck_ignores_probe_timestamps_but_reports_real_changes(tmp_path):
    """Periodic evidence is delta-based, not a 60-second full inventory push."""

    _, path, _ = package(tmp_path)
    pack = scan_packs(path)[0]
    node = NodeRuntime(NodeConfig(workspace_root=tmp_path / "ws"))
    node._packs = {(pack.manifest.name, pack.manifest.version): pack}
    node._ws = object()
    node._safe_send = AsyncMock()

    current = {"status": "satisfied", "requires": {}, "issues": [], "env": "test"}

    async def report(_manifest):
        # Deliberately omit checked_at: the runtime adds a fresh timestamp each
        # pass, which used to make every pack appear changed forever.
        return dict(current)

    node._environment_report = report
    await node._refresh_environment_evidence(send_update=False)
    first_checked_at = next(iter(node._environment_evidence.values()))["checked_at"]

    await node._refresh_environment_evidence(send_update=True)
    assert node._safe_send.await_count == 0
    assert next(iter(node._environment_evidence.values()))["checked_at"] == first_checked_at

    current["status"] = "missing"
    current["issues"] = [{"item": "package:demo", "actual": "未安装"}]
    await node._refresh_environment_evidence(send_update=True)

    assert node._safe_send.await_count == 1
    kind, message = node._safe_send.call_args.args
    assert kind == "environment_updated"
    assert len(message.evidence) == 1
    assert message.evidence[0].report["status"] == "missing"


@pytest.mark.asyncio
async def test_routine_capability_check_does_not_rebroadcast_unchanged_evidence(tmp_path):
    _, path, _ = package(tmp_path)
    pack = scan_packs(path)[0]
    node = NodeRuntime(NodeConfig(workspace_root=tmp_path / "ws"))
    node._packs = {(pack.manifest.name, pack.manifest.version): pack}
    node._ws = object()
    node._safe_send = AsyncMock()

    async def report(_manifest, *, fresh=False, check_batch_id=None):
        return {"status": "satisfied", "requires": {}, "issues": [], "env": "test"}

    node._environment_report = report
    await node._refresh_environment_evidence(send_update=False)
    await node._capability_reply(
        CapabilityReq(
            req_id="routine",
            algorithm_name=pack.manifest.name,
            algorithm_version=pack.manifest.version,
        )
    )

    # The RPC response is returned, but no environment_updated echo is sent.
    assert node._safe_send.await_count == 1
    assert node._safe_send.call_args.args[0] == "capability_resp"

    node._safe_send.reset_mock()
    await node._capability_reply(
        CapabilityReq(
            req_id="fresh",
            algorithm_name=pack.manifest.name,
            algorithm_version=pack.manifest.version,
            fresh=True,
        )
    )
    assert node._safe_send.await_count == 1
    assert node._safe_send.call_args.args[0] == "capability_resp"

    current = {"status": "missing", "requires": {}, "issues": [{"item": "env"}], "env": "test"}

    async def changed_report(_manifest, *, fresh=False, check_batch_id=None):
        return dict(current)

    node._environment_report = changed_report
    node._safe_send.reset_mock()
    await node._capability_reply(
        CapabilityReq(
            req_id="changed",
            algorithm_name=pack.manifest.name,
            algorithm_version=pack.manifest.version,
        )
    )
    assert [call.args[0] for call in node._safe_send.call_args_list] == [
        "environment_updated",
        "capability_resp",
    ]


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


def test_api_reports_an_unoffered_exact_package_as_missing(tmp_path):
    """A removed version is actionable absence, not unknown probe evidence."""
    _, path, _ = package(tmp_path)
    selected = session([scan_packs(path)[0]])

    with TestClient(create_app(db_path=tmp_path / "test.sqlite")) as client:
        client.app.state.registry._sessions["n"] = selected
        graph = WorkflowGraph(
            nodes=[
                GraphNode(
                    id="a", algorithm_name="demo", algorithm_version="9.9.9", assigned_node_id="n"
                )
            ]
        )
        response = client.post("/api/capabilities/check", json={"graph": graph.model_dump()})

    assert response.status_code == 200
    assert response.json()["nodes"]["a"]["status"] == "missing"
    assert response.json()["nodes"]["a"]["issues"] == [
        {
            "item": "algorithm_package",
            "required": "demo@9.9.9",
            "actual": "执行节点未提供此版本",
            "remedy": "在该节点安装此确切版本，或将工作流改为已提供的版本",
        }
    ]


def test_catalog_reports_runtime_and_node_local_environment_path(tmp_path):
    """The catalog keeps a package runtime separate from a node-local prefix."""
    _, path, _ = package(tmp_path)
    path.write_text(path.read_text() + "\nrequires: {}\n")
    pack = scan_packs(path)[0]
    selected = session([pack])
    selected.packs[0].requires = {}
    selected.packs[0].runtime = {"mode": "conda", "env": "test"}
    selected.envs = {"test": "/opt/conda/envs/test"}

    with TestClient(create_app(db_path=tmp_path / "test.sqlite")) as client:
        client.app.state.registry._sessions["n"] = selected
        catalog = client.get("/api/pack-catalog").json()

    assert catalog[0]["runtime"] == {"mode": "conda", "env": "test"}
    assert catalog[0]["node_environments"] == {
        "n": {"runtime": {"mode": "conda", "env": "test"}, "path": "/opt/conda/envs/test"}
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
    rpc_kinds = []

    async def rpc(app, selected, kind, request, *args, **kwargs):
        rpc_kinds.append(kind)
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

        graph.nodes.append(graph.nodes[0].model_copy(update={"id": "b"}))

        async def save():
            return await client.app.state.workflows.save_draft(
                workflow_id=None, name="environment regression", graph=graph
            )

        row = client.portal.call(save)
        base = f"/api/workflows/{row.workflow_id}"
        evidence = [
            {"job_id": "historical", "graph_node_id": "a", "state": "done", "created_ts": 1}
        ]
        monkeypatch.setattr(
            "hololab.gateway.canvas_jobs.canvas_jobs", AsyncMock(return_value=evidence)
        )
        rpc_kinds.clear()
        analysis = client.post(base + "/analyze", json={"graph": graph.model_dump()})
        assert analysis.status_code == 200, analysis.text
        assert analysis.json()["canvas_jobs"][0]["state"] == "done"
        # Canvas analysis is a local staleness/diff calculation.  Environment
        # badges have their own endpoint; polling /analyze must not fan out
        # fresh capability RPCs across every node in a workflow.
        assert "environment_checks" not in analysis.json()
        assert rpc_kinds == []
        prepared = client.post(base + "/prepare", json={})
        assert prepared.status_code == 200, prepared.text
        assert len(prepared.json()["run_blockers"]) == 2
        assert prepared.json()["graph"]["execution"]["prepared_nodes"] == []
        for headers in ({}, {"X-Preparation-Token": prepared.json()["token"]}):
            run = client.post(base + "/run", headers=headers)
            assert run.status_code == 422, run.text
            assert "a (demo@1.0.0)" in run.json()["detail"]
            assert "b (demo@1.0.0)" in run.json()["detail"]
        single = client.post(base + "/prepare", json={"node_id": "a"})
        assert single.status_code == 422
        assert "a (demo@1.0.0)" in single.text
        assert "b (demo@1.0.0)" not in single.text
        assert client.get("/api/jobs").json() == []
        selected.ws.send_text.assert_not_called()

        selected.environment_checks = False
        old_node = client.post("/api/capabilities/check", json={"graph": graph.model_dump()}).json()
        assert old_node["nodes"]["a"]["status"] == "unknown"
        assert "升级节点" in old_node["nodes"]["a"]["issues"][0]["actual"]

        graph.nodes[0].algorithm_name = "unavailable-pack"
        unavailable = client.post(
            "/api/capabilities/check", json={"graph": graph.model_dump()}
        ).json()
        assert unavailable["nodes"]["a"]["status"] == "missing"  # Exact package absent.


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


def test_display_retains_node_path_preparation_error(tmp_path, monkeypatch):
    pack = make_pack(tmp_path)
    selected = session([pack])
    selected.environment_checks = True
    selected.packs[0].requires = pack.manifest.requires.model_dump()

    async def rpc(app, node, kind, request, *args, **kwargs):
        if kind == "capability_req":
            return {"report": {"status": "satisfied", "issues": []}}
        assert kind == "prepare_params_req"
        if request.params["path"] == "":
            return {"error": "Parameter path: enter a nonempty path string"}
        return {"params": {"path": "/normalized/good"}}

    monkeypatch.setattr("hololab.gateway.app._await_node_reply", rpc)
    with TestClient(create_app(db_path=tmp_path / "paths.sqlite")) as client:
        client.app.state.registry._sessions["n"] = selected
        catalog = client.app.state.registry.catalog_json()
        catalog[0]["params"] = {"path": {"type": "path"}}
        monkeypatch.setattr(client.app.state.registry, "catalog_json", lambda: catalog)
        graph = WorkflowGraph(
            nodes=[
                GraphNode(
                    id=nid,
                    algorithm_name="demo",
                    algorithm_version="1.0.0",
                    assigned_node_id="n",
                    params={"path": path},
                )
                for nid, path in [("bad", ""), ("good", "good")]
            ]
        )

        async def exercise():
            result = await prepare_execution(client.app, graph, strict=False)
            assert result.execution["environment_checks"]["bad"]["status"] == "unknown"
            assert result.execution["environment_checks"]["good"]["status"] == "satisfied"
            assert result.execution["prepared_nodes"] == ["good"]
            assert result.nodes[1].params["path"] == "/normalized/good"
            with pytest.raises(ValueError, match="Parameter path"):
                await prepare_execution(client.app, graph, required={"bad"})

        client.portal.call(exercise)
