"""frame-extraction 0.2.2 makes skip zero-based without changing migrated output."""

from __future__ import annotations

import hashlib
import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1] / "hololab" / "builtin_packs"


def load(version: str):
    spec = importlib.util.spec_from_file_location(
        "extract_frames_" + version.replace(".", "_"),
        ROOT / f"frame-extraction@{version}" / "extract_frames.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ffmpeg(*args):
    return subprocess.check_output(["ffmpeg", "-v", "error", "-y", *map(str, args)])


def pixels(directory: Path) -> list[str]:
    from PIL import Image

    return [
        hashlib.sha256(Image.open(path).convert("RGB").tobytes()).hexdigest()
        for path in sorted(directory.glob("p_*.png"))
    ]


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "input.mp4"
    ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=size=96x64:rate=30", "-frames:v", "18",
        "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p", path,
    )
    return path


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_zero_keeps_every_frame_and_one_skips_every_other(tmp_path: Path, source: Path):
    pack = load("0.2.2")
    all_frames = tmp_path / "all"
    alternate = tmp_path / "alternate"

    assert pack.extract_frames(source, all_frames, skip=0) == 18
    assert pack.extract_frames(source, alternate, skip=1) == 9
    assert len(pixels(all_frames)) == 18
    assert pixels(alternate) == pixels(all_frames)[::2]
    assert pack._build_select_expr(0, 0, 1) is None
    assert "mod(n-0,2)" in pack._build_select_expr(0, 0, 2)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_upgrade_from_020_preserves_the_old_full_frame_output(tmp_path: Path, source: Path):
    old, new = load("0.2.0"), load("0.2.2")
    old_output, new_output = tmp_path / "old", tmp_path / "new"

    assert old.extract_frames(source, old_output, frame_skip=1) == 18
    assert new.extract_frames(source, new_output, skip=0) == 18
    assert pixels(new_output) == pixels(old_output)


def test_manifest_and_validation_are_zero_based(tmp_path: Path):
    from hololab.manifest import load_manifest

    manifest, _ = load_manifest(ROOT / "frame-extraction@0.2.2" / "manifest.yaml")
    assert manifest.version == "0.2.2"
    assert manifest.params["skip"].default == 0
    assert manifest.params["skip"].min == 0
    assert manifest.params["skip"].description == "0 = 不丢弃任何帧（全量抽帧）；N = 每隔 N 帧取一帧。"
    with pytest.raises(ValueError, match="skip must be >= 0"):
        load("0.2.2").extract_frames(tmp_path / "missing.mp4", tmp_path / "out", skip=-1)
