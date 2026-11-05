"""Image contracts: every leaf checked once by stat + header (no pixel decode), no sampling,
first failure reported in traversal order.

Also covers scalar ``min``/``max`` enforcement for dispatched parameters.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image, ImageFile

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


def leaf_check_count(monkeypatch):
    calls = []
    real = data_contract.check_image_header

    def counting(path, *, label):
        calls.append(str(path))
        return real(path, label=label)

    monkeypatch.setattr(data_contract, "check_image_header", counting)
    return calls


def test_every_leaf_checked_once_without_decoding(tmp_path, monkeypatch):
    root = write_frames(tmp_path / "frames", 9)
    calls = leaf_check_count(monkeypatch)
    loads = []
    real_load = ImageFile.ImageFile.load
    monkeypatch.setattr(
        ImageFile.ImageFile, "load", lambda self: loads.append(1) or real_load(self)
    )
    validate_path(root, array_spec(), label="out")
    assert sorted(calls) == sorted(str(p) for p in root.iterdir())
    assert loads == []  # header only: pixel data is never decoded


def test_missing_leaf_is_rejected(tmp_path):
    root = write_frames(tmp_path / "frames", 3)
    (root / "p_0000000000000003.png").symlink_to(tmp_path / "gone.png")
    with pytest.raises(ContractError, match="missing array element or broken link"):
        validate_path(root, array_spec(), label="out")
    with pytest.raises(ContractError, match="missing input/output path"):
        validate_path(tmp_path / "absent", array_spec(), label="out")


def test_empty_file_is_rejected(tmp_path):
    root = write_frames(tmp_path / "frames", 3)
    (root / "p_0000000000000001.png").write_bytes(b"")
    with pytest.raises(ContractError, match=r"p_0000000000000001\.png: empty file"):
        validate_path(root, array_spec(), label="out")


def test_text_file_is_rejected_with_unchanged_message(tmp_path):
    root = write_frames(tmp_path / "frames", 6)
    bad = root / "p_0000000000000002.png"
    bad.write_text("this is not an image\n")
    with pytest.raises(ContractError) as err:
        validate_path(root, array_spec(), label="output.frames")
    assert str(err.value) == (
        f"output.frames: invalid image file {bad}: cannot identify image file {str(bad)!r}"
    )


def test_bad_header_is_rejected(tmp_path):
    root = write_frames(tmp_path / "frames", 3)
    target = root / "p_0000000000000000.png"
    data = bytearray(target.read_bytes())
    data[1] ^= 0x01  # PNG signature
    target.write_bytes(bytes(data))
    with pytest.raises(ContractError, match="cannot identify image file"):
        validate_path(root, array_spec(), label="out")


def test_zero_size_header_is_rejected(tmp_path, monkeypatch):
    root = write_frames(tmp_path / "frames", 2)
    real_open = Image.open

    def zero_size(path, *args, **kwargs):
        img = real_open(path, *args, **kwargs)
        img._size = (0, 6)
        return img

    monkeypatch.setattr(Image, "open", zero_size)
    with pytest.raises(ContractError, match="invalid image size 0x6"):
        validate_path(root, array_spec(), label="out")


def test_multi_frame_still_rejected(tmp_path):
    root = write_frames(tmp_path / "frames", 3)
    frames = [Image.new("RGB", (8, 6), c) for c in ((1, 2, 3), (4, 5, 6))]
    frames[0].save(root / "p_0000000000000001.png", save_all=True, append_images=frames[1:])
    with pytest.raises(ContractError, match="multi-frame image is not a single image"):
        validate_path(root, array_spec(), label="out")


def test_truncated_pixel_data_is_not_detected(tmp_path):
    """Documented limit of the header-only check: a body cut short still passes."""
    root = tmp_path / "frames"
    root.mkdir()
    Image.effect_noise((512, 512), 64).convert("RGB").save(root / "p_0000000000000000.png")
    target = root / "p_0000000000000000.png"
    data = target.read_bytes()
    target.write_bytes(data[: len(data) // 2])
    validate_path(root, array_spec(), label="out")  # passes: pixels are not decoded
    with Image.open(target) as img, pytest.raises(OSError):
        img.load()  # a full decode would have caught it


def test_no_file_descriptor_leak(tmp_path):
    root = write_frames(tmp_path / "frames", 50)
    before = len(os.listdir("/proc/self/fd"))
    for _ in range(5):
        validate_path(root, array_spec(), label="out")
    assert len(os.listdir("/proc/self/fd")) == before


def test_4k_png_header_check_is_fast(tmp_path):
    root = tmp_path / "frames"
    root.mkdir()
    Image.effect_noise((3840, 2160), 40).convert("RGB").save(
        root / "p_0000000000000000.png", compress_level=1
    )
    for i in range(1, 20):
        os.link(root / "p_0000000000000000.png", root / f"p_{i:016d}.png")
    start = time.perf_counter()
    validate_path(root, array_spec(), label="out")
    per_leaf = (time.perf_counter() - start) / 20
    assert per_leaf < 0.01  # header only; a full 4K decode is ~130 ms


def test_first_invalid_leaf_in_traversal_order_is_reported(tmp_path):
    root = write_frames(tmp_path / "frames", 12, corrupt={3, 8})
    with pytest.raises(ContractError, match=r"p_0000000000000003\.png"):
        validate_path(root, array_spec(), label="out")


def test_last_leaf_still_checked(tmp_path):
    root = write_frames(tmp_path / "frames", 12, corrupt={11})
    with pytest.raises(ContractError, match=r"p_0000000000000011\.png"):
        validate_path(root, array_spec(), label="out")


@pytest.mark.parametrize(
    ("spec", "value", "ok"),
    [
        ({"type": "int", "default": 1, "min": 1}, 0, False),
        ({"type": "int", "default": 1, "min": 1}, 1, True),
        ({"type": "int", "default": 1, "max": 9}, 10, False),
        ({"type": "float", "default": 1.0, "min": 0.001}, 0.0, False),
        ({"type": "float", "default": 1.0, "min": 0.001}, 0.5, True),
        ({"type": "int", "default": 1, "min": 1}, None, False),
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
