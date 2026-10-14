"""Replacement must not silently misassign supervision to a camera."""

import subprocess
import sys
from pathlib import Path

from PIL import Image

SCRIPT = Path(__file__).resolve().parents[1] / "packs/replace-colmap-images@0.1.0/run.py"


def setup_case(tmp_path):
    source, fixed, output = [tmp_path / x for x in ("source", "fixed", "output")]
    (source / "images").mkdir(parents=True)
    (source / "sparse/0").mkdir(parents=True)
    (source / "sparse/0/images.txt").write_text("camera poses must remain byte-identical\n")
    fixed.mkdir()
    for i in range(2):
        Image.new("RGB", (8, 8), (100, 0, 0)).save(source / "images" / f"frame_{i:06d}.png")
        Image.new("RGB", (8, 8), (0, 200, 0)).save(fixed / f"frame_{i:06d}", format="PNG")
    return source, fixed, output


def run_case(source, fixed, output):
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--colmap",
            str(source),
            "--images",
            str(fixed),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
    )


def test_replaces_gt_and_keeps_camera_bytes(tmp_path):
    source, fixed, output = setup_case(tmp_path)
    assert run_case(source, fixed, output).returncode == 0
    assert (source / "sparse/0/images.txt").read_bytes() == (
        output / "sparse/0/images.txt"
    ).read_bytes()
    assert Image.open(output / "images/frame_000000.png").getpixel((0, 0)) == (0, 200, 0)
    assert Image.open(source / "images/frame_000000.png").getpixel((0, 0)) == (100, 0, 0)


def test_rejects_missing_frame(tmp_path):
    source, fixed, output = setup_case(tmp_path)
    (fixed / "frame_000001").unlink()
    assert run_case(source, fixed, output).returncode != 0


def test_rejects_changed_intrinsic_resolution(tmp_path):
    source, fixed, output = setup_case(tmp_path)
    Image.new("RGB", (4, 4)).save(fixed / "frame_000000", format="PNG")
    assert run_case(source, fixed, output).returncode != 0


def test_rejects_duplicate_stem(tmp_path):
    source, fixed, output = setup_case(tmp_path)
    Image.new("RGB", (8, 8)).save(fixed / "frame_000000.png")
    assert run_case(source, fixed, output).returncode != 0
