"""Output contracts: every image leaf decoded exactly once, in parallel, with unchanged rejection.

Also covers scalar ``min``/``max`` enforcement for dispatched parameters.
"""

from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from hololab.manifest import data_contract
from hololab.manifest.data_contract import ContractError, validate_path
from hololab.manifest.schema import OutputSpec, ParamSpec


def array_spec():
    return OutputSpec(tags=["image"], arrayed=True, storage="dir", dim_labels=["frame"])


def write_frames(root, count, corrupt=()):
    root.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        path = root / f"p_{i:016d}.png"
        if i in corrupt:
            path.write_bytes(b"not a png")
        else:
            Image.new("RGB", (8, 6), (i, 2 * i, 3)).save(path)
    return root


def test_every_leaf_decoded_once_in_parallel(tmp_path):
    root = write_frames(tmp_path / "frames", 9)
    opened = []
    real_open = Image.open

    def counting_open(path, *args, **kwargs):
        opened.append(path)
        return real_open(path, *args, **kwargs)

    with patch.object(Image, "open", counting_open):
        validate_path(root, array_spec(), label="out")
    assert sorted(map(str, opened)) == sorted(str(p) for p in root.iterdir())
    assert data_contract.IMAGE_DECODE_WORKERS > 1


def test_first_invalid_leaf_in_traversal_order_is_reported(tmp_path):
    root = write_frames(tmp_path / "frames", 12, corrupt={3, 8})
    with pytest.raises(ContractError, match="p_0000000000000003.png"):
        validate_path(root, array_spec(), label="out")


def test_last_leaf_still_checked(tmp_path):
    root = write_frames(tmp_path / "frames", 12, corrupt={11})
    with pytest.raises(ContractError, match="invalid image file"):
        validate_path(root, array_spec(), label="out")


@pytest.mark.parametrize(
    ("spec", "value", "ok"),
    [
        ({"type": "int", "default": 1, "min": 1}, 0, False),
        ({"type": "int", "default": 1, "min": 1}, 1, True),
        ({"type": "int", "default": 1, "max": 9}, 10, False),
        ({"type": "float", "default": 1.0, "min": 0.001}, 0.0, False),
        ({"type": "float", "default": 1.0, "min": 0.001}, 0.5, True),
        ({"type": "int", "default": 1, "min": 1}, None, True),
        ({"type": "bool", "default": False}, True, True),
    ],
)
def test_scalar_min_max(spec, value, ok):
    param = ParamSpec.model_validate(spec)
    if ok:
        param.validate_scalar_value("p", value)
    else:
        with pytest.raises(ValueError, match="parameter 'p'"):
            param.validate_scalar_value("p", value)


def _contract_node(tmp_path, shell, params=None):
    from hololab.manifest.schema import Manifest
    from hololab.node.config import NodeConfig
    from hololab.node.packs import LoadedPack
    from hololab.node.runtime import NodeRuntime

    manifest = Manifest.model_validate(
        {
            "apiVersion": "hololab.dev/v1",
            "kind": "Algorithm",
            "name": "contract-once",
            "version": "1.0.0",
            "outputs": {"out": {"tags": ["image"], "arrayed": True, "storage": "dir",
                                "dim_labels": ["frame"]}},
            "params": params or {},
            "runtime": {"env": "test"},
            "exec": {"shell": shell},
        }
    )
    pack = LoadedPack(manifest, tmp_path, tmp_path / "manifest.yaml", "test", tmp_path)
    node = NodeRuntime(NodeConfig(workspace_root=tmp_path / "ws", envs={"test": sys.prefix}))
    node._node_id = "test"
    node._packs[(manifest.name, manifest.version)] = pack
    node._env_cache[sys.prefix] = dict(os.environ)
    node._safe_send = AsyncMock()
    node._send = AsyncMock()
    node._send_job_fail = AsyncMock()
    return node, manifest


def _run(node, manifest, params=None):
    from hololab.protocol.messages import JobAssign

    return node._run_job(
        JobAssign(job_id="j", workflow_id="w", algorithm_name=manifest.name,
                  algorithm_version=manifest.version, input_handles={}, params=params or {}),
        asyncio.Event(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [False, True])
async def test_job_output_validated_once_and_invalid_never_registered(tmp_path, invalid):
    source = write_frames(tmp_path / "src", 5, corrupt={4} if invalid else ())
    node, manifest = _contract_node(tmp_path, f'cp "{source}"/*.png "{{{{ outputs.out }}}}/"')
    import hololab.node.runtime as runtime

    calls = []
    real = runtime.validate_ports

    def counting(paths, specs, *, direction, params=None):
        calls.append(direction)
        return real(paths, specs, direction=direction, params=params)

    with patch.object(runtime, "validate_ports", counting):
        await _run(node, manifest)
    registered = [c for c in node._safe_send.call_args_list if c.args[0] == "handle_register"]
    if invalid:
        assert not registered
        assert "output contract violation" in node._send_job_fail.call_args.kwargs["message"]
    else:
        node._send_job_fail.assert_not_called()
        assert len(registered) == 1
        assert calls.count("output") == 1  # was 2 before (batch check + register-time check)


@pytest.mark.asyncio
async def test_node_rejects_scalar_below_min(tmp_path):
    node, manifest = _contract_node(
        tmp_path, 'true', params={"skip": {"type": "int", "default": 1, "min": 1}}
    )
    await _run(node, manifest, params={"skip": 0})
    message = node._send_job_fail.call_args.kwargs["message"]
    assert "'skip'" in message and ">= 1" in message
    assert not [c for c in node._safe_send.call_args_list if c.args[0] == "handle_register"]
