import asyncio
from unittest.mock import AsyncMock

import pytest

from hololab.content_scope import capture
from hololab.node import runtime as runtime_module
from hololab.node.config import NodeConfig
from hololab.node.executor import ExecResult
from hololab.node.packs import scan_packs
from hololab.node.runtime import NodeRuntime
from hololab.protocol.messages import JobAssign, JobFailReason
from tests.test_content_scope import package


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["hot", "cold", "ignore", "assets", "legacy"])
async def test_queue_edit_is_checked_at_spawn(tmp_path, monkeypatch, change):
    root, manifest, baselines = package(tmp_path)
    record = capture(manifest, baselines)
    node = NodeRuntime(NodeConfig(workspace_root=tmp_path / "ws", envs={"test": "/unused"}))
    pack = scan_packs(manifest)[0]
    node._packs = {(pack.manifest.name, pack.manifest.version): pack}
    node._exec_semaphore = asyncio.Semaphore(0)
    node._send_job_fail = AsyncMock()
    acknowledged = asyncio.Event()

    async def send(kind, payload):
        if kind == "job_ack":
            acknowledged.set()

    node._send = AsyncMock(side_effect=send)
    node._safe_send = AsyncMock()
    # A nonzero result exits before output publication; no real algorithm process is run.
    executor = AsyncMock(return_value=ExecResult(exit_code=1))
    monkeypatch.setattr(runtime_module, "run_subprocess", executor)
    assign = JobAssign(
        job_id="j",
        workflow_id="w",
        algorithm_name=pack.manifest.name,
        algorithm_version=pack.manifest.version,
        code_identity=None if change == "legacy" else record,
    )
    task = asyncio.create_task(node._run_job(assign, asyncio.Event()))
    try:
        await asyncio.wait_for(acknowledged.wait(), 3)
        if change in ("hot", "legacy"):
            (root / "stg_state.py").write_text("print('changed while queued')")
        elif change == "cold":
            (root / "thirdparty/lib.py").write_text("cold = 999")
        elif change == "ignore":
            (root / ".packignore").write_text("*\n")
        elif change == "assets":
            (root / "assets/large.bin").write_bytes(b"changed")
        node._exec_semaphore.release()
        await asyncio.wait_for(task, 3)
        if change in ("hot", "ignore"):
            executor.assert_not_called()
            fail = node._send_job_fail.call_args
            assert fail.args[1] == JobFailReason.USER_ERROR
            assert "提交时=" in fail.kwargs["message"]
            assert "Fork" in fail.kwargs["message"]
        else:
            executor.assert_awaited_once()
            plan = executor.call_args.args[0]
            assert plan.working_dir == root
            assert str(root / "stg_state.py") in plan.shell
        if change == "cold":
            after = capture(manifest, baselines)
            assert after["cold"] == record["cold"]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_release_and_manual_baseline_cli(tmp_path):
    import json

    from typer.testing import CliRunner

    from hololab.cli import app

    root, manifest, baselines = package(tmp_path)
    runner = CliRunner()
    args = [str(manifest.parent), "--baseline-dir", str(baselines.directory)]
    released = runner.invoke(app, ["pack", "release", *args])
    assert released.exit_code == 0, released.output
    first = json.loads(released.output)
    (root / "thirdparty/lib.py").write_text("cold = 10")
    manual = runner.invoke(app, ["pack", "rebaseline", *args])
    assert manual.exit_code == 0, manual.output
    second = json.loads(manual.output)
    assert second["cold"]["baseline_at"] > first["cold"]["baseline_at"]
    assert second["cold"]["sha256"] != first["cold"]["sha256"]


@pytest.mark.asyncio
async def test_capture_uses_selected_node_and_history_keeps_identity(monkeypatch):
    from types import SimpleNamespace

    from hololab.gateway.preparation import capture_code_identities, historical_execution
    from hololab.gateway.workflows import GraphNode, WorkflowGraph
    from hololab.protocol.messages import PackInventoryEntry

    inventory = PackInventoryEntry(name="demo", version="1.0.0", manifest_hash="m")
    assert inventory.root == "." and inventory.cold == []
    session = SimpleNamespace(content_identity=True, packs=[inventory])
    registry = SimpleNamespace(get_session=lambda _: session, catalog_json=lambda: [])
    app = SimpleNamespace(state=SimpleNamespace(registry=registry))
    graph = WorkflowGraph(
        nodes=[
            GraphNode(
                id="n",
                algorithm_name="demo",
                algorithm_version="1.0.0",
                assigned_node_id="selected",
            )
        ],
        execution={"version": 1},
    )
    record = {"source": "demo@1.0.0", "hot": {"sha256": "submitted"}}
    rpc = AsyncMock(return_value={"record": record})
    monkeypatch.setattr("hololab.gateway.app._await_node_reply", rpc)
    await capture_code_identities(app, graph)
    assert rpc.call_args.args[1] is session
    assert graph.nodes[0].code_identity == record
    snapshot = SimpleNamespace(graph=graph)
    frozen = await historical_execution(app, snapshot)
    assert frozen.nodes[0].code_identity == record
    frozen.nodes[0].code_identity["hot"]["sha256"] = "changed copy"
    assert snapshot.graph.nodes[0].code_identity == record
    session.content_identity = False
    await capture_code_identities(app, graph)
    assert graph.nodes[0].code_identity is None  # Old protocol, explicit unrecorded path.
    registry.catalog_json = lambda: [{"name": "demo", "version": "1.0.0", "root": ".."}]
    with pytest.raises(ValueError, match="upgraded"):
        await capture_code_identities(app, graph)
