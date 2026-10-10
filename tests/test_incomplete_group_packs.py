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


def test_missing_cam3_uses_the_six_present_cameras(tmp_path: Path) -> None:
    """The shared SfM list has seven rows, while this group deliberately has six."""
    pytest.importorskip("numpy")
    # This regression exercises only filename/pose intersection, not image
    # sampling. Keep the unit test runnable in the lightweight gateway env.
    sys.modules.setdefault("cv2", types.ModuleType("cv2"))
    kornia = types.ModuleType("kornia")
    geometry = types.ModuleType("kornia.geometry")
    calibration = types.ModuleType("kornia.geometry.calibration")
    calibration.distort_points = lambda *args, **kwargs: None
    geometry.calibration = calibration
    kornia.geometry = geometry
    sys.modules.setdefault("kornia", kornia)
    sys.modules.setdefault("kornia.geometry", geometry)
    sys.modules.setdefault("kornia.geometry.calibration", calibration)
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
    assert len((tmp_path / "manual" / "images.txt").read_text().splitlines()) == 13
