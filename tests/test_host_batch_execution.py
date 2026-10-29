"""Host batches must execute every argv literally and stop on failure."""

import asyncio
from types import SimpleNamespace

import pytest

from hololab.manifest.render import RenderedManifest
from hololab.node.config import NodeConfig
from hololab.node.executor import run_subprocess
from hololab.node.runtime import _make_exec_plan


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_first", [False, True])
async def test_host_batch_arguments_and_failure(tmp_path, fail_first):
    entry = tmp_path / "entry with spaces.py"
    entry.write_text(
        "import pathlib, sys\n"
        "pathlib.Path(sys.argv[1]).write_text(sys.argv[2])\n"
        "sys.exit(int(sys.argv[3]))\n"
    )
    first, second = tmp_path / "first", tmp_path / "second"
    literal = "spaces; $(touch INJECTED) `echo bad`"
    rendered = RenderedManifest(
        shell="",
        working_dir=None,
        idempotency_marker=None,
        preview_paths={},
        preview_globs={},
        node_viewer_live_globs={},
        progress_status_file=None,
        python_arg_batches=[
            [str(first), literal, "2" if fail_first else "0"],
            [str(second), "second element", "0"],
        ],
    )
    pack = SimpleNamespace(
        pack_dir=tmp_path,
        package_root=tmp_path,
        manifest=SimpleNamespace(
            runtime=SimpleNamespace(mode="host"),
            exec=SimpleNamespace(python=entry.name),
            progress=None,
        ),
    )
    plan = _make_exec_plan(rendered, NodeConfig(), "", pack, cached_env=None, cpu_mask=None)
    result = await run_subprocess(
        plan,
        on_log=lambda *args: None,
        on_progress=lambda *args: None,
        cancel_event=asyncio.Event(),
    )
    assert result.exit_code == (2 if fail_first else 0)
    assert first.read_text() == literal
    assert not (tmp_path / "INJECTED").exists()
    if fail_first:
        assert not second.exists()
    else:
        assert second.read_text() == "second element"


@pytest.mark.asyncio
async def test_runtime_renders_and_runs_both_host_elements(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    from hololab.manifest.schema import Manifest
    from hololab.node import runtime as mod
    from hololab.node.packs import LoadedPack
    from hololab.protocol.messages import JobAssign

    entry = tmp_path / "copy.py"
    entry.write_text(
        "import pathlib, sys\n"
        'pathlib.Path(sys.argv[2], "result.txt").write_text('
        'pathlib.Path(sys.argv[1], "source.txt").read_text())\n'
    )
    manifest = Manifest.model_validate(
        {
            "apiVersion": "hololab.dev/v1",
            "kind": "Algorithm",
            "name": "builtin/batch-test",
            "version": "0.1.0",
            "inputs": {"src": {"tags": ["any"]}},
            "outputs": {"out": {"tags": ["any"]}},
            "runtime": {"mode": "host"},
            "exec": {"python": "copy.py", "args": ["{{ inputs.src }}", "{{ outputs.out }}"]},
        }
    )
    pack = LoadedPack(manifest, tmp_path, tmp_path / "manifest.yaml", "test", tmp_path)
    node = mod.NodeRuntime(NodeConfig(workspace_root=tmp_path / "ws"))
    node._packs[(manifest.name, manifest.version)] = pack
    node._safe_send = AsyncMock()
    node._send = AsyncMock()
    node._send_job_fail = AsyncMock()
    node._register_output_handles = AsyncMock(return_value={})
    monkeypatch.setattr(mod, "_preflight_resources", lambda *a, **kw: None)
    inputs = []
    for eid in ("a", "b"):
        src = tmp_path / eid
        src.mkdir()
        (src / "source.txt").write_text(eid)
        inputs.append({"src": str(src)})
    await node._run_job(
        JobAssign(
            job_id="batch",
            workflow_id="w",
            algorithm_name=manifest.name,
            algorithm_version=manifest.version,
            shard_element_id="a",
            shard_element_ids=["a", "b"],
            shard_input_paths=inputs,
            shard_output_prefix=str(tmp_path / "aggregate"),
        ),
        asyncio.Event(),
    )
    node._send_job_fail.assert_not_called()
    assert any(c.args[0] == "job_done" for c in node._send.call_args_list)
    calls = node._register_output_handles.call_args_list
    assert len(calls) == 2
    from pathlib import Path

    assert [Path(c.args[2]["out"], "result.txt").read_text() for c in calls] == ["a", "b"]
