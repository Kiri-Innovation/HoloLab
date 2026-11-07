"""Single-decode prefetch preserves native RGB hashes and bounded memory."""

import hashlib
import importlib.util
import threading
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

PACK = Path(__file__).parents[1] / "community_packs/birefnet-mask@2.2.4"
spec = importlib.util.spec_from_file_location("birefnet224", PACK / "run.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_native_hash_before_resize_and_one_decode(tmp_path, monkeypatch):
    pixels = np.arange(12 * 8 * 3, dtype=np.uint8).reshape(8, 12, 3)
    path = tmp_path / "source.png"
    Image.fromarray(pixels).save(path)
    original = mod.read_rgb
    calls = []

    def read(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(mod, "read_rgb", read)
    tensor, record, size, native_size = mod.prepare_frame(
        0,
        path,
        transform=lambda image: np.array(image),
        max_width=6,
        start_frame=7,
        skip=2,
    )
    assert len(calls) == 1
    assert record["rgb_sha256"] == hashlib.sha256(pixels.tobytes()).hexdigest()
    assert size == (6, 4) and native_size == (12, 8)
    assert tensor.shape == (4, 6, 3)
    assert record["source_frame_index"] == 7


def test_prefetch_is_bounded_ordered_and_propagates_failure(tmp_path, monkeypatch):
    filled = threading.Event()
    calls = []

    def prepare(index, path, **kwargs):
        calls.append(index)
        if index == 3:
            filled.set()
        if index == 5:
            raise ValueError("bad frame")
        return index

    monkeypatch.setattr(mod, "prepare_frame", prepare)
    with mod.prefetched_frames(list(range(234)), depth=4) as frames:
        assert filled.wait(5)
        assert calls == [0, 1, 2, 3]
        assert [next(frames) for _ in range(5)] == list(range(5))
        with pytest.raises(ValueError, match="bad frame"):
            next(frames)
    assert len(calls) <= 9


def test_manifest_defaults_and_shell():
    from hololab.manifest import load_manifest

    manifest, _ = load_manifest(PACK / "manifest.yaml")
    assert manifest.version == "2.2.4"
    assert manifest.params["model_input_size"].default == 1024
    assert manifest.params["compress_level"].default == 1
    assert "--model-input-size" in manifest.exec.shell
    assert "--compress-level" in manifest.exec.shell
