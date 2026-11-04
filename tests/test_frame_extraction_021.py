"""frame-extraction 0.2.1 keeps 0.2.0 selection, numbering and decoded pixels.

Legacy encoder arguments (``png_compression=-1``, ``png_predictor=none``) must
reproduce 0.2.0 files byte for byte; the faster defaults may change PNG bytes but
never decoded RGB.
"""

from __future__ import annotations

import hashlib
import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "hololab" / "builtin_packs"


def load(version):
    spec = importlib.util.spec_from_file_location(
        "extract_frames_" + version.replace(".", "_"),
        ROOT / f"frame-extraction@{version}" / "extract_frames.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ffmpeg(*args):
    return subprocess.check_output(["ffmpeg", "-v", "error", "-y", *map(str, args)])


def pixels(directory):
    from PIL import Image

    return [
        hashlib.sha256(Image.open(p).convert("RGB").tobytes()).hexdigest()
        for p in sorted(directory.glob("p_*.png"))
    ]


def file_bytes(directory):
    return [hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(directory.glob("p_*.png"))]


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "input.mp4"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=96x64:rate=30", "-frames:v", "18",
           "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p", path)
    return path


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_frames": 7},
        {"start_frame": 3, "end_frame": 15, "frame_skip": 4},
        {"max_width": 48, "max_frames": 3},
    ],
)
def test_legacy_bytes_and_default_pixels_match_020(tmp_path, source, kwargs):
    old, new = load("0.2.0"), load("0.2.1")
    ref, legacy, fast = tmp_path / "ref", tmp_path / "legacy", tmp_path / "fast"
    n_ref = old.extract_frames(source, ref, **kwargs)
    n_legacy = new.extract_frames(source, legacy, png_compression=-1, png_predictor="none", **kwargs)
    n_fast = new.extract_frames(source, fast, **kwargs)
    assert n_ref == n_legacy == n_fast > 0
    names = [sorted(p.name for p in d.iterdir()) for d in (ref, legacy, fast)]
    assert names[0] == names[1] == names[2]
    assert file_bytes(legacy) == file_bytes(ref)
    assert pixels(fast) == pixels(ref)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_truncation_warning_and_progress(tmp_path, source, capsys):
    load("0.2.1").extract_frames(source, tmp_path / "out", max_frames=5)
    out = capsys.readouterr().out
    assert "可抽 18 帧" in out and "丢弃末尾 13 帧" in out
    assert "[ITER 5/5]" in out


def test_invalid_parameters_are_rejected(tmp_path):
    new = load("0.2.1")
    for kwargs in ({"frame_skip": 0}, {"max_frames": 0}, {"png_compression": 10},
                   {"png_predictor": "best"}, {"decode_threads": -1}):
        with pytest.raises(ValueError):
            new.extract_frames(tmp_path / "missing.mp4", tmp_path / "out", **kwargs)


def test_expected_frame_count():
    count = load("0.2.1").expected_frame_count
    assert count(234, 0, None, 1, 200) == (234, 200)
    assert count(234, 0, None, 1, 1000) == (234, 234)
    assert count(234, 10, 100, 3, 20) == (30, 20)
    assert count(None, 0, None, 1, 50) == (None, 50)


def test_manifest_defaults_and_bundled_identity():
    from hololab.bundled import verified_record
    from hololab.manifest import load_manifest

    path = ROOT / "frame-extraction@0.2.1" / "manifest.yaml"
    manifest, _ = load_manifest(path)
    assert manifest.version == "0.2.1"
    assert manifest.params["png_compression"].default == 1
    assert manifest.params["png_predictor"].default == "sub"
    assert all(manifest.params[k].default == 0 for k in ("decode_threads", "encode_threads", "filter_threads"))
    assert manifest.params["skip"].min == 1
    assert verified_record(path)["sha256"]
    assert verified_record(ROOT / "frame-extraction@0.2.0" / "manifest.yaml")["sha256"]
