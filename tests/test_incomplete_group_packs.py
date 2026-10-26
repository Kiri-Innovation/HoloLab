"""Regression: an intermittent rig camera drop does not kill a whole shard."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

PACKS = Path(__file__).resolve().parent.parent / "packs"


def _module(path: Path, name: str):
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


def _images_txt(names: list[str]) -> str:
    rows = ["# Image list with two lines of data per image:"]
    for index, name in enumerate(names, 1):
        rows.extend([f"{index} 1 0 0 0 0 0 0 {index} {name}", ""])
    return "\n".join(rows) + "\n"


def test_missing_cam3_uses_the_six_present_cameras(tmp_path: Path, monkeypatch) -> None:
    """The shared SfM list has seven rows, while this group deliberately has six."""
    pytest.importorskip("numpy")
    pytest.importorskip("torch")
    # This regression exercises only filename/pose intersection, not image
    # sampling. Keep the unit test runnable in the lightweight gateway env.
    monkeypatch.setitem(sys.modules, "cv2", types.ModuleType("cv2"))
    kornia = types.ModuleType("kornia")
    geometry = types.ModuleType("kornia.geometry")
    calibration = types.ModuleType("kornia.geometry.calibration")
    calibration.distort_points = lambda *args, **kwargs: None
    geometry.calibration = calibration
    kornia.geometry = geometry
    monkeypatch.setitem(sys.modules, "kornia", kornia)
    monkeypatch.setitem(sys.modules, "kornia.geometry", geometry)
    monkeypatch.setitem(sys.modules, "kornia.geometry.calibration", calibration)
    worker = _module(
        PACKS / "image-undistort@0.5.2" / "und_worker.py", "und_worker_052_missing_group"
    )
    tri = _module(PACKS / "colmap-triangulate@0.7.4" / "triangulate.py", "tri_074_missing_group")

    all_names = [f"cam{i}.jpg" for i in (0, 1, 2, 3, 4, 5, 7)]
    present = [name for name in all_names if name != "cam3.jpg"]
    staged = []
    for name in present:
        p = tmp_path / name
        p.write_bytes(b"image")
        staged.append(p)
    sfm = tmp_path / "sfm"
    sfm.mkdir()
    (sfm / "images.txt").write_text(_images_txt(all_names))
    (sfm / "cameras.txt").write_text(
        "\n".join(f"{i} SIMPLE_RADIAL 10 10 8 5 5 0" for i in range(1, 8)) + "\n"
    )

    rewritten, _rewrites, absent = worker.stage_sparse_prior(sfm, tmp_path / "prior", staged)
    assert len(rewritten) == 6
    assert absent == ["cam3"]

    cams = tri.parse_cameras_txt(sfm / "cameras.txt")
    poses = {
        Path(name).stem: (tri.np.array([1.0, 0, 0, 0]), tri.np.zeros(3), i)
        for i, name in enumerate(all_names, 1)
    }
    resolved = tri.prefill_db(tmp_path / "input.db", tmp_path / "manual", present, cams, poses)
    assert [name for name, _ in resolved] == present
    assert [row[-1] for row in tri.parse_images_txt(tmp_path / "manual" / "images.txt")] == present


def test_triangulate_preserves_gapped_sfm_image_ids(tmp_path: Path, monkeypatch) -> None:
    """A missing cam5 must not cause cam7's original IMAGE_ID 7 to become 6."""
    pytest.importorskip("numpy")
    monkeypatch.setitem(sys.modules, "cv2", types.ModuleType("cv2"))
    tri = _module(PACKS / "colmap-triangulate@0.7.5" / "triangulate.py", "tri_075_gapped_ids")

    image_ids = [1, 2, 3, 4, 5, 7]
    names = ["cam0.jpg", "cam1.jpg", "cam2.jpg", "cam3.jpg", "cam4.jpg", "cam7.jpg"]
    cams = {image_id: ("SIMPLE_RADIAL", 10, 10, [8.0, 5.0, 5.0, 0.0]) for image_id in image_ids}
    poses = {
        Path(name).stem: (image_id, tri.np.array([1.0, 0, 0, 0]), tri.np.zeros(3), image_id)
        for image_id, name in zip(image_ids, names, strict=True)
    }

    tri.prefill_db(tmp_path / "input.db", tmp_path / "manual", names, cams, poses)
    actual_ids = [row[0] for row in tri.parse_images_txt(tmp_path / "manual" / "images.txt")]
    assert actual_ids == image_ids


def test_merge_filters_only_dangling_tracks_and_drops_empty_points(tmp_path: Path) -> None:
    merge = _module(PACKS / "merge-colmap@0.4.0" / "merge.py", "merge_040_tracks")
    source = tmp_path / "points3D.txt"
    target = tmp_path / "fixed.txt"
    source.write_text(
        "# header preserved\n"
        "1 0 0 0 1 2 3 0.1 1 10 6 11 7 12\n"
        "2 0 0 0 1 2 3 0.2 6 20\n"
        "3 0 0 0 1 2 3 0.3 2 30\n"
    )

    assert merge._sanitize_points3d_tracks(source, target, {1, 2, 3, 4, 5, 7}) == (2, 1)
    assert target.read_text() == (
        "# header preserved\n1 0 0 0 1 2 3 0.1 1 10 7 12\n3 0 0 0 1 2 3 0.3 2 30\n"
    )


def test_merge_keeps_valid_points_byte_identical(tmp_path: Path) -> None:
    merge = _module(PACKS / "merge-colmap@0.4.0" / "merge.py", "merge_040_exact_copy")
    source = tmp_path / "points3D.txt"
    target = tmp_path / "copied.txt"
    payload = b"# odd spacing stays exact\r\n1  0 0 0 1 2 3 0.1  1 10 7 12\r\n"
    source.write_bytes(payload)

    assert merge._sanitize_points3d_tracks(source, target, {1, 7}) == (0, 0)
    assert target.read_bytes() == payload
