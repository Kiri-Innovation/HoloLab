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
split_v020_mod = _load_module("_split_v020_mod", PACKS_ROOT / "colmap-split@0.2.0" / "split.py")
undistort_v020_mod = _load_module(
    "_undistort_v020_mod", PACKS_ROOT / "image-undistort@0.2.0" / "undistort.py"
)


# ---------------------------------------------------------------------------
# Manifest shape
# ---------------------------------------------------------------------------


def test_colmap_split_v010_shape() -> None:
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
    m, _ = load_manifest(PACKS_ROOT / "image-undistort@0.1.0" / "manifest.yaml")
    assert m.name == "image-undistort"
    assert m.version == "0.1.0"
    assert m.arrayable is True
    # Two scalar-per-shard inputs — cameras.txt + a frame_sequence.
    assert m.inputs["cameras"].tags == ["colmap-cameras-txt"]
    assert m.inputs["cameras"].arrayed is False
    assert m.inputs["images"].tags == ["frame_sequence"]
    assert m.inputs["images"].arrayed is False
    # Two outputs — PINHOLE camera + undistorted image dir.
    assert m.outputs["cameras"].tags == ["colmap-cameras-txt"]
    assert m.outputs["undistorted"].tags == ["frame_sequence"]
    assert m.source_entry == "undistort.py"
    # Docs must cite the framework-level zip contract so an operator wiring
    # the node knows arrayfy is required for scalar broadcast into arrays.
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


# ---------------------------------------------------------------------------
# colmap-split@0.2.0 — shape
# ---------------------------------------------------------------------------


def test_colmap_split_v020_shape() -> None:
    m, _ = load_manifest(PACKS_ROOT / "colmap-split@0.2.0" / "manifest.yaml")
    assert m.name == "colmap-split"
    assert m.version == "0.2.0"
    assert m.arrayable is True
    assert m.inputs["cams"].tags == ["colmap-cams"]
    assert m.inputs["cams"].arrayed is False
    # Three outputs: PINHOLE txt + distortion JSON + poses txt.
    assert m.outputs["pinhole_intrinsics"].tags == ["colmap-cameras-txt"]
    assert m.outputs["distortion"].tags == ["camera-distortion-json"]
    assert m.outputs["poses"].tags == ["colmap-images-txt"]
    for p in ("pinhole_intrinsics", "distortion", "poses"):
        assert m.outputs[p].arrayed is False, p
    assert m.source_entry == "split.py"


def test_image_undistort_v020_shape() -> None:
    m, _ = load_manifest(PACKS_ROOT / "image-undistort@0.2.0" / "manifest.yaml")
    assert m.name == "image-undistort"
    assert m.version == "0.2.0"
    assert m.arrayable is True
    # Three typed inputs.
    assert m.inputs["pinhole_intrinsics"].tags == ["colmap-cameras-txt"]
    assert m.inputs["pinhole_intrinsics"].arrayed is False
    assert m.inputs["distortion"].tags == ["camera-distortion-json"]
    assert m.inputs["distortion"].arrayed is False
    assert m.inputs["images"].tags == ["frame_sequence"]
    assert m.inputs["images"].arrayed is False
    # Outputs unchanged from @0.1.0.
    assert m.outputs["cameras"].tags == ["colmap-cameras-txt"]
    assert m.outputs["undistorted"].tags == ["frame_sequence"]
    assert m.source_entry == "undistort.py"
    assert m.docs and "_discover_element_ids" in m.docs


# ---------------------------------------------------------------------------
# colmap-split@0.2.0 — behaviour: split + reassembly roundtrip
# ---------------------------------------------------------------------------


def _split_v020_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    src = tmp_path / "cams"
    src.mkdir()
    (src / "cameras.txt").write_text(_SFM_FIXTURE_CAMERAS)
    (src / "images.txt").write_text(_SFM_FIXTURE_IMAGES)
    ph_out = tmp_path / "out_pinhole"
    dist_out = tmp_path / "out_dist"
    poses_out = tmp_path / "out_poses"
    argv = [
        "split.py",
        "--cams", str(src),
        "--pinhole-intrinsics-out", str(ph_out),
        "--distortion-out", str(dist_out),
        "--poses-out", str(poses_out),
    ]
    old_argv = sys.argv
    sys.argv = argv
    try:
        rc = split_v020_mod.main()
    finally:
        sys.argv = old_argv
    assert rc == 0
    return ph_out, dist_out, poses_out


def test_split_v020_pinhole_cameras_is_pinhole(tmp_path: Path) -> None:
    """pinhole_intrinsics output must be COLMAP PINHOLE — no distortion model."""
    ph_out, _, _ = _split_v020_fixture(tmp_path)
    body = (ph_out / "cameras.txt").read_text()
    data_lines = [ln for ln in body.splitlines() if ln and not ln.startswith("#")]
    assert len(data_lines) == 1
    parts = data_lines[0].split()
    assert parts[1] == "PINHOLE"
    # 4 params: fx fy cx cy
    assert len(parts) == 8, parts  # id model W H fx fy cx cy


def test_split_v020_distortion_json_opencv_params(tmp_path: Path) -> None:
    """distortion JSON must carry model='OPENCV' and the four distortion coefficients."""
    import json

    _, dist_out, _ = _split_v020_fixture(tmp_path)
    data = json.loads((dist_out / "distortion.json").read_text())
    assert data["schema_version"] == 1
    cam = data["cameras"][0]
    assert cam["camera_id"] == 1
    assert cam["model"] == "OPENCV"
    k1, k2, p1, p2 = cam["params"]
    assert pytest.approx(k1, rel=1e-6) == 0.00279
    assert pytest.approx(k2, rel=1e-6) == 0.00080
    assert pytest.approx(p1, rel=1e-6) == -7.4e-05
    assert pytest.approx(p2, rel=1e-6) == 0.00016


def test_split_v020_poses_observations_stripped(tmp_path: Path) -> None:
    _, _, poses_out = _split_v020_fixture(tmp_path)
    body = (poses_out / "images.txt").read_text()
    non_blank = [ln for ln in body.splitlines() if ln and not ln.startswith("#")]
    assert len(non_blank) == 2


def test_split_v020_roundtrip_opencv(tmp_path: Path) -> None:
    """split → reassemble → params numerically equal to the original OPENCV entry."""
    ph_out, dist_out, _ = _split_v020_fixture(tmp_path)
    reassembled = tmp_path / "reassembled.txt"
    undistort_v020_mod.reassemble_cameras_txt(
        ph_out / "cameras.txt",
        dist_out / "distortion.json",
        reassembled,
    )
    expected_params = [1462.10, 1454.28, 1352.0, 1014.0, 0.00279, 0.00080, -7.4e-05, 0.00016]
    for ln in reassembled.read_text().splitlines():
        if not ln or ln.startswith("#"):
            continue
        parts = ln.split()
        assert parts[1] == "OPENCV", f"model should be OPENCV, got {parts[1]}"
        params = [float(p) for p in parts[4:]]
        assert params == pytest.approx(expected_params, rel=1e-6), params


def test_split_v020_roundtrip_simple_opencv(tmp_path: Path) -> None:
    """SIMPLE_OPENCV (single focal length) roundtrips correctly via normalisation."""
    import json

    src = tmp_path / "cams"
    src.mkdir()
    # SIMPLE_OPENCV: f cx cy k1 k2 p1 p2 (7 params, 3 pinhole)
    (src / "cameras.txt").write_text(
        "1 SIMPLE_OPENCV 1920 1080 800.5 960 540 0.01 -0.003 0.0001 -0.0002\n"
    )
    (src / "images.txt").write_text("1 1 0 0 0 0 0 0 1 a.png\n\n")
    ph_out = tmp_path / "ph"
    dist_out = tmp_path / "dist"
    poses_out = tmp_path / "poses"
    sys.argv = [
        "split.py",
        "--cams", str(src),
        "--pinhole-intrinsics-out", str(ph_out),
        "--distortion-out", str(dist_out),
        "--poses-out", str(poses_out),
    ]
    rc = split_v020_mod.main()
    assert rc == 0

    # Pinhole line must be PINHOLE with fx=fy=f.
    ph_line = next(
        ln for ln in (ph_out / "cameras.txt").read_text().splitlines()
        if ln and not ln.startswith("#")
    )
    ph_parts = ph_line.split()
    assert ph_parts[1] == "PINHOLE"
    assert pytest.approx(float(ph_parts[4])) == 800.5  # fx
    assert pytest.approx(float(ph_parts[5])) == 800.5  # fy = fx = f

    # Distortion JSON.
    dist = json.loads((dist_out / "distortion.json").read_text())
    assert dist["cameras"][0]["model"] == "SIMPLE_OPENCV"
    assert len(dist["cameras"][0]["params"]) == 4

    # Reassembly.
    reassembled = tmp_path / "r.txt"
    undistort_v020_mod.reassemble_cameras_txt(
        ph_out / "cameras.txt", dist_out / "distortion.json", reassembled
    )
    r_line = next(
        ln for ln in reassembled.read_text().splitlines()
        if ln and not ln.startswith("#")
    )
    r_parts = r_line.split()
    assert r_parts[1] == "SIMPLE_OPENCV"
    params = [float(p) for p in r_parts[4:]]
    expected = [800.5, 960.0, 540.0, 0.01, -0.003, 0.0001, -0.0002]
    assert params == pytest.approx(expected, rel=1e-6)
