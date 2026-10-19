from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from hololab.gateway.registry import NodeRegistry
from hololab.manifest.schema import ParamSpec


def test_enum_declaration_and_raw_values():
    with pytest.raises(ValidationError):
        ParamSpec(type="enum")
    spec = ParamSpec(type="enum", values=[1, "1", "auto"], default=1)
    assert spec.values == [1, "1", "auto"]
    for value in spec.values:
        spec.validate_enum_value("mode", value)
    for value in [True, None, "missing", 2]:
        with pytest.raises(ValueError, match=r"parameter 'mode'.*allowed values"):
            spec.validate_enum_value("mode", value)
    # Reading old persisted values does not invoke execution validation.
    assert ParamSpec(type="enum", values=["new"], default="old").default == "old"


def test_enum_catalog_preserves_values(tmp_path):
    import yaml

    source = Path("packs/single-video-source@0.1.0/manifest.yaml")
    data = yaml.safe_load(source.read_text())
    data["params"]["mode"] = {"type": "enum", "values": [1, "auto"], "default": 1, "pinned": True}
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(data))
    registry = NodeRegistry(None)
    registry._sessions["node"] = SimpleNamespace(
        node_id="node",
        pack_dirs=[],
        packs=[
            SimpleNamespace(
                name=data["name"],
                version=data["version"],
                manifest_hash="test",
                manifest_path=str(path),
                source_dir=str(tmp_path),
            )
        ],
    )
    spec = registry.catalog_json()[0]["params"]["mode"]
    assert spec["pinned"] is True
    assert spec["values"] == [1, "auto"]
    assert type(spec["values"][0]) is int


def test_runtime_rejects_enum_before_environment_or_process():
    import asyncio
    from unittest.mock import AsyncMock

    from hololab.node.runtime import NodeRuntime

    fail = AsyncMock()
    runtime = SimpleNamespace(
        _packs={
            ("demo", "1"): SimpleNamespace(
                manifest=SimpleNamespace(params={"mode": ParamSpec(type="enum", values=["a", "b"])})
            )
        },
        _send_job_fail=fail,
    )
    assign = SimpleNamespace(
        algorithm_name="demo",
        algorithm_version="1",
        job_id="test",
        params={"mode": "legacy"},
        intrinsic=None,
    )
    asyncio.run(NodeRuntime._run_job(runtime, assign, asyncio.Event()))
    fail.assert_awaited_once()
    message = fail.call_args.kwargs["message"]
    assert all(value in message for value in ["mode", "legacy", "['a', 'b']"])
    assert assign.params == {"mode": "legacy"}


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [2, "auto", "legacy"])
async def test_real_enum_dispatch(tmp_path, value):
    import asyncio
    import os
    import sys
    from unittest.mock import AsyncMock

    from hololab.manifest.schema import Manifest
    from hololab.node.config import NodeConfig
    from hololab.node.packs import LoadedPack
    from hololab.node.runtime import NodeRuntime
    from hololab.protocol.messages import JobAssign

    result = tmp_path / "executed.txt"
    manifest = Manifest.model_validate(
        {
            "apiVersion": "hololab.dev/v1",
            "kind": "Algorithm",
            "name": "enum-smoke",
            "version": "1.0.0",
            "inputs": {},
            "outputs": {"out": {"tags": ["text"], "storage": "file"}},
            "params": {"mode": {"type": "enum", "values": [2, "auto"]}},
            "runtime": {"env": "test"},
            "exec": {
                "shell": 'printf "%s" "{{ params.mode }}" > '
                + str(result)
                + "; cp "
                + str(result)
                + ' "{{ outputs.out }}"'
            },
        }
    )
    node = NodeRuntime(NodeConfig(workspace_root=tmp_path / "ws", envs={"test": sys.prefix}))
    node._node_id = "test"
    node._packs[(manifest.name, manifest.version)] = LoadedPack(
        manifest, tmp_path, tmp_path / "manifest.yaml", "test", tmp_path
    )
    node._env_cache[sys.prefix] = dict(os.environ)
    node._safe_send = AsyncMock()
    node._send = AsyncMock()
    node._send_job_fail = AsyncMock()
    assign = JobAssign(
        job_id="enum-job",
        workflow_id="enum-workflow",
        algorithm_name=manifest.name,
        algorithm_version=manifest.version,
        input_handles={},
        params={"mode": value},
    )
    await node._run_job(assign, asyncio.Event())
    if value == "legacy":
        assert not result.exists()
        node._send_job_fail.assert_awaited_once()
    else:
        node._send_job_fail.assert_not_called()
        assert result.read_text() == str(value)
        assert type(assign.params["mode"]) is type(value)
        assert any(call.args[0] == "job_done" for call in node._send.call_args_list)
