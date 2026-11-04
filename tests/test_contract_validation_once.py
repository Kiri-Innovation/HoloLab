"""Image contracts: every output leaf decoded exactly once in a worker process, with unchanged
rejection; inputs already decoded by this process get a structural verify() instead.

Also covers scalar ``min``/``max`` enforcement for dispatched parameters.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image, ImageFile, PngImagePlugin

from hololab.manifest import data_contract
from hololab.manifest.data_contract import ContractError, validate_path, validate_ports
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


@pytest.fixture
def in_process_pool(monkeypatch):
    """Run worker checks on threads so in-process patches can observe them."""
    pool = ThreadPoolExecutor(4)
    monkeypatch.setattr(data_contract, "_decode_pool", lambda: pool)
    monkeypatch.setattr(data_contract, "_verified", {})
    monkeypatch.setattr(data_contract, "_sidecars", {})
    yield pool
    pool.shutdown()


def count_calls(monkeypatch):
    calls = {"open": [], "load": 0, "verify": 0}
    real_open, real_load = Image.open, ImageFile.ImageFile.load
    real_verify = PngImagePlugin.PngImageFile.verify

    def opened(path, *args, **kwargs):
        calls["open"].append(str(path))
        return real_open(path, *args, **kwargs)

    def load(self):
        calls["load"] += 1
        return real_load(self)

    def verify(self):
        calls["verify"] += 1
        return real_verify(self)

    monkeypatch.setattr(Image, "open", opened)
    monkeypatch.setattr(ImageFile.ImageFile, "load", load)
    monkeypatch.setattr(PngImagePlugin.PngImageFile, "verify", verify)
    return calls


def test_every_leaf_decoded_once(tmp_path, in_process_pool, monkeypatch):
    root = write_frames(tmp_path / "frames", 9)
    calls = count_calls(monkeypatch)
    validate_path(root, array_spec(), label="out")
    assert sorted(calls["open"]) == sorted(str(p) for p in root.iterdir())
    assert calls["load"] == 9 and calls["verify"] == 0


def test_decodes_run_in_one_shared_process_pool(tmp_path):
    root = write_frames(tmp_path / "frames", 9)
    validate_path(root, array_spec(), label="out")
    pool = data_contract._decode_pool()
    assert isinstance(pool, ProcessPoolExecutor)
    assert data_contract.IMAGE_DECODE_WORKERS >= 8
    validate_path(root, array_spec(), label="out")
    assert data_contract._decode_pool() is pool  # reused, not one pool per call
    assert all(p.pid != os.getpid() for p in pool._processes.values())


def test_error_message_unchanged(tmp_path):
    root = write_frames(tmp_path / "frames", 6, corrupt={2})
    bad = root / "p_0000000000000002.png"
    with pytest.raises(ContractError) as err:
        validate_path(root, array_spec(), label="output.frames")
    assert str(err.value) == (
        f"output.frames: invalid image file {bad}: cannot identify image file {str(bad)!r}"
    )


def test_multi_frame_still_rejected(tmp_path):
    root = write_frames(tmp_path / "frames", 3)
    frames = [Image.new("RGB", (8, 6), c) for c in ((1, 2, 3), (4, 5, 6))]
    frames[0].save(root / "p_0000000000000001.png", save_all=True, append_images=frames[1:])
    with pytest.raises(ContractError, match="multi-frame image is not a single image"):
        validate_path(root, array_spec(), label="out")


def test_broken_worker_pool_falls_back_in_process(tmp_path, monkeypatch):
    class Broken:
        def submit(self, *args):
            future = Future()
            future.set_exception(BrokenProcessPool("worker killed"))
            return future

        def shutdown(self, **kwargs):
            pass

    broken = Broken()
    monkeypatch.setattr(data_contract, "_pool", broken)
    monkeypatch.setattr(data_contract, "_verified", {})
    root = write_frames(tmp_path / "frames", 10, corrupt={7})
    with pytest.raises(ContractError, match=r"p_0000000000000007\.png"):
        validate_path(root, array_spec(), label="out")
    assert data_contract._pool is None  # dropped; the next call builds a fresh pool
    (root / "p_0000000000000007.png").unlink()
    validate_path(root, array_spec(), label="out")


def test_verified_input_gets_structural_check_only(tmp_path, in_process_pool, monkeypatch):
    root = write_frames(tmp_path / "frames", 5)
    validate_ports({"o": str(root)}, {"o": array_spec()}, direction="output")
    calls = count_calls(monkeypatch)
    validate_ports({"i": str(root)}, {"i": array_spec()}, direction="input")
    assert calls["load"] == 0 and calls["verify"] == 5


def test_unknown_input_is_fully_decoded(tmp_path, in_process_pool, monkeypatch):
    root = write_frames(tmp_path / "frames", 5)  # never validated by this process
    calls = count_calls(monkeypatch)
    validate_ports({"i": str(root)}, {"i": array_spec()}, direction="input")
    assert calls["load"] == 5 and calls["verify"] == 0


def test_outputs_and_plain_calls_always_fully_decode(tmp_path, in_process_pool, monkeypatch):
    root = write_frames(tmp_path / "frames", 4)
    validate_path(root, array_spec(), label="out")
    calls = count_calls(monkeypatch)
    validate_ports({"o": str(root)}, {"o": array_spec()}, direction="output")
    validate_path(root, array_spec(), label="out")
    assert calls["load"] == 8 and calls["verify"] == 0


def test_changed_file_after_verification_is_fully_decoded(tmp_path, in_process_pool, monkeypatch):
    root = write_frames(tmp_path / "frames", 4)
    validate_path(root, array_spec(), label="out")
    target = root / "p_0000000000000001.png"
    st = target.stat()
    time.sleep(0.05)
    # Same size, mtime restored: only inode/ctime can tell it changed.
    Image.new("RGB", (8, 6), (250, 1, 1)).save(tmp_path / "other.png")
    data = (tmp_path / "other.png").read_bytes()
    if len(data) == st.st_size:
        target.write_bytes(data)
    else:
        target.write_bytes(target.read_bytes())
    os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns))
    calls = count_calls(monkeypatch)
    validate_ports({"i": str(root)}, {"i": array_spec()}, direction="input")
    assert calls["load"] == 1 and calls["verify"] == 3


def test_corrupted_after_verification_is_rejected(tmp_path, in_process_pool):
    root = write_frames(tmp_path / "frames", 4)
    validate_path(root, array_spec(), label="out")
    (root / "p_0000000000000002.png").write_bytes(b"not a png")
    with pytest.raises(ContractError, match=r"p_0000000000000002\.png"):
        validate_ports({"i": str(root)}, {"i": array_spec()}, direction="input")


def test_truncated_verified_input_is_rejected(tmp_path, in_process_pool):
    root = write_frames(tmp_path / "frames", 3)
    validate_path(root, array_spec(), label="out")
    target = root / "p_0000000000000000.png"
    data = target.read_bytes()
    target.write_bytes(data[: len(data) // 2])
    with pytest.raises(ContractError, match=r"p_0000000000000000\.png"):
        validate_ports({"i": str(root)}, {"i": array_spec()}, direction="input")


def test_symlinked_view_of_verified_leaves(tmp_path, in_process_pool, monkeypatch):
    root = write_frames(tmp_path / "frames", 3)
    validate_path(root, array_spec(), label="out")
    view = tmp_path / "view"
    view.mkdir()
    for p in sorted(root.iterdir()):
        (view / p.name).symlink_to(p)
    calls = count_calls(monkeypatch)
    validate_ports({"i": str(view)}, {"i": array_spec()}, direction="input")
    assert calls["load"] == 0 and calls["verify"] == 3


def test_first_invalid_leaf_in_traversal_order_is_reported(tmp_path):
    root = write_frames(tmp_path / "frames", 12, corrupt={3, 8})
    with pytest.raises(ContractError, match=r"p_0000000000000003\.png"):
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

    def counting(paths, specs, *, direction, params=None, **kwargs):
        calls.append(direction)
        return real(paths, specs, direction=direction, params=params, **kwargs)

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
