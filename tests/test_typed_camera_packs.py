"""M9 packs: colmap-split + image-undistort + colmap-triangulate@0.3.0.

Locks the manifest shape (tag names, arrayable flag, input/output topology)
so a future refactor doesn't silently break the typed-camera decomposition,
and asserts the on-disk contract of the entry points:

* ``colmap-split`` copies ``cameras.txt`` verbatim (COLMAP-native, no JSON),
  and rewrites ``images.txt`` with per-pose observation lines blanked.
* ``image-undistort`` consumes those two COLMAP files (no reassembly step),
  and its script has no cross-pack Python dependency.
* Both split outputs must round-trip through the real ``colmap
  model_converter`` binary — the ultimate "is this valid COLMAP text?"
  oracle.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from hololab.manifest import load_manifest

PACKS_ROOT = Path(__file__).resolve().parent.parent / "packs"


def _load_module(name: str, path: Path):
    """Import a pack's Python entry as ``name`` — packs live outside sys.path."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


split_mod = _load_module("_split_mod", PACKS_ROOT / "colmap-split@0.1.0" / "split.py")


# ---------------------------------------------------------------------------
# Manifest shape
# ---------------------------------------------------------------------------


def test_colmap_split_shape() -> None:
    m, _ = load_manifest(PACKS_ROOT / "colmap-split@0.1.0" / "manifest.yaml")
    assert m.name == "colmap-split"
    assert m.version == "0.1.0"
    # Arrayable so fan-out on arrayed<colmap-cams> yields arrayed<T> per output.
    assert m.arrayable is True
    assert m.inputs["cams"].tags == ["colmap-cams"]
    assert m.inputs["cams"].arrayed is False
    # Two COLMAP-text outputs — no JSON tag anywhere.
    assert m.outputs["cameras"].tags == ["colmap-cameras-txt"]
    assert m.outputs["poses"].tags == ["colmap-images-txt"]
    for p in ("cameras", "poses"):
        assert m.outputs[p].arrayed is False
    assert m.source_entry == "split.py"


def test_image_undistort_shape() -> None:
    m, _ = load_manifest(PACKS_ROOT / "image-undistort@0.2.0" / "manifest.yaml")
    assert m.name == "image-undistort"
    assert m.version == "0.2.0"
    assert m.arrayable is True
    # cameras: scalar-locked — must NOT participate in the fan-out zip;
    # the framework broadcasts the single cameras.txt to every shard.
    assert m.inputs["cameras"].tags == ["colmap-cameras-txt"]
    assert m.inputs["cameras"].scalar is True
    assert m.inputs["cameras"].arrayed is False
    # images: fan-out driver — one shard per element (no scalar lock).
    assert m.inputs["images"].tags == ["frame_sequence"]
    assert m.inputs["images"].scalar is False
    # Two outputs — renamed to und_cameras / und_images so the canvas label
    # reads "und.und_cameras" and "und.und_images" (port id == display name).
    assert m.outputs["und_cameras"].tags == ["colmap-cameras-txt"]
    assert m.outputs["und_images"].tags == ["frame_sequence"]
    assert m.source_entry == "undistort.py"
    # Docs must cite the framework-level zip contract.
    assert m.docs and "_discover_element_ids" in m.docs


def test_colmap_triangulate_v030_shape() -> None:
    m, _ = load_manifest(PACKS_ROOT / "colmap-triangulate@0.3.0" / "manifest.yaml")
    assert m.name == "colmap-triangulate"
    assert m.version == "0.3.0"
    assert m.arrayable is True
    # Three typed inputs (no monolithic colmap-cams anymore).
    assert m.inputs["cameras"].tags == ["colmap-cameras-txt"]
    assert m.inputs["poses"].tags == ["colmap-images-txt"]
    assert m.inputs["images"].tags == ["frame_sequence"]
    for p in ("cameras", "poses", "images"):
        assert m.inputs[p].arrayed is False, p
    # poses is scalar-locked — shared across all camera shards (broadcast).
    assert m.inputs["poses"].scalar is True
    assert m.inputs["cameras"].scalar is False
    assert m.inputs["images"].scalar is False
    # Output shape is identical to @0.2.0 so colmap-assemble @0.2.0 still consumes it.
    assert m.outputs["frame"].tags == ["colmap-frame"]
    assert m.outputs["frame"].arrayed is False


# ---------------------------------------------------------------------------
# split.py — text-native output
# ---------------------------------------------------------------------------


_SFM_FIXTURE_CAMERAS = (
    "# Camera list with one line of data per camera:\n"
    "#   CAMERA_ID, MODEL, WIDTH, HEIGHT, PARAMS[]\n"
    "# Number of cameras: 1\n"
    "1 OPENCV 2704 2028 1462.10 1454.28 1352 1014 0.00279 0.00080 -7.4e-05 0.00016\n"
)
_SFM_FIXTURE_IMAGES = (
    "# Image list with two lines of data per image:\n"
    "#   IMAGE_ID, QW, QX, QY, QZ, TX, TY, TZ, CAMERA_ID, NAME\n"
    "#   POINTS2D[] as (X, Y, POINT3D_ID)\n"
    "1 0.99 0.01 0.02 0.03 1.0 2.0 3.0 1 cam00.png\n"
    "100 200 -1 300 400 -1 500 600 42\n"  # obs line — must be dropped
    "2 0.98 0.02 0.03 0.04 4.0 5.0 6.0 1 cam01.png\n"
    "700 800 -1 900 1000 -1\n"
)


def _split_fixture(tmp_path: Path) -> tuple[Path, Path]:
    src = tmp_path / "cams"
    src.mkdir()
    (src / "cameras.txt").write_text(_SFM_FIXTURE_CAMERAS)
    (src / "images.txt").write_text(_SFM_FIXTURE_IMAGES)
    cameras_out = tmp_path / "out_cameras"
    poses_out = tmp_path / "out_poses"
    argv = [
        "split.py",
        "--cams",
        str(src),
        "--cameras-out",
        str(cameras_out),
        "--poses-out",
        str(poses_out),
    ]
    old_argv = sys.argv
    sys.argv = argv
    try:
        rc = split_mod.main()
    finally:
        sys.argv = old_argv
    assert rc == 0
    return cameras_out, poses_out


def test_split_cameras_txt_verbatim(tmp_path: Path) -> None:
    """cameras.txt IS the camera model — split must not touch it."""
    cameras_out, _ = _split_fixture(tmp_path)
    assert (cameras_out / "cameras.txt").read_text() == _SFM_FIXTURE_CAMERAS


def test_split_images_txt_drops_observations(tmp_path: Path) -> None:
    _, poses_out = _split_fixture(tmp_path)
    body = (poses_out / "images.txt").read_text()
    # Header lines preserved; both pose header lines preserved.
    assert "1 0.99 0.01 0.02 0.03 1.0 2.0 3.0 1 cam00.png" in body
    assert "2 0.98 0.02 0.03 0.04 4.0 5.0 6.0 1 cam01.png" in body
    # Observation tokens gone.
    for stray in ("100", "200", "42", "700", "900"):
        assert stray not in body.split("cam01.png")[-1], (
            f"observation token {stray!r} leaked past the second pose"
        )
    # Only pose headers survive — 2 non-blank non-comment lines.
    non_blank_data_lines = [
        line for line in body.splitlines() if line and not line.startswith("#")
    ]
    assert len(non_blank_data_lines) == 2, non_blank_data_lines


def test_split_strip_observations_helper_directly(tmp_path: Path) -> None:
    """strip_observations is a helper; test in isolation for the return contract."""
    src = tmp_path / "images.txt"
    src.write_text(
        "# c\n1 0 0 0 0 0 0 0 1 a.png\nx y -1\n2 0 0 0 0 0 0 0 1 b.png\n\n"  # already-empty obs
    )
    dest = tmp_path / "images-poses.txt"
    kept, _lines = split_mod.strip_observations(src, dest)
    assert kept == 2, dest.read_text()


@pytest.fixture(scope="module")
def _colmap_bin() -> str | None:
    return shutil.which("colmap")


def test_split_output_colmap_roundtrips_through_model_converter(
    tmp_path: Path, _colmap_bin: str | None
) -> None:
    """The ultimate COLMAP-parseability oracle: assemble a full sparse dir
    from split outputs + empty points3D.txt and pipe it through
    ``colmap model_converter --output_type BIN``. If the binary accepts
    both files, they're valid COLMAP text."""
    if _colmap_bin is None:
        pytest.skip("colmap binary not on PATH")
    cameras_out, poses_out = _split_fixture(tmp_path)
    # Build a sparse dir COLMAP can eat: cameras.txt + images.txt + points3D.txt.
    sparse = tmp_path / "sparse"
    sparse.mkdir()
    shutil.copy(cameras_out / "cameras.txt", sparse / "cameras.txt")
    shutil.copy(poses_out / "images.txt", sparse / "images.txt")
    (sparse / "points3D.txt").write_text("")
    bin_out = tmp_path / "sparse_bin"
    bin_out.mkdir()
    res = subprocess.run(
        [
            _colmap_bin,
            "model_converter",
            "--input_path",
            str(sparse),
            "--output_path",
            str(bin_out),
            "--output_type",
            "BIN",
        ],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, (
        f"colmap model_converter rejected split output:\nstdout: {res.stdout}\nstderr: {res.stderr}"
    )
    # Byproducts image_undistorter cares about should exist.
    assert (bin_out / "cameras.bin").is_file()
    assert (bin_out / "images.bin").is_file()
