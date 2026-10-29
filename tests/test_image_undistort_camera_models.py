"""Camera-model normalization regression tests for image-undistort@0.5.4."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

PACK = Path(__file__).resolve().parent.parent / "community_packs/image-undistort@0.5.4"


def _worker(monkeypatch: pytest.MonkeyPatch):
    # Parsing camera rows does not execute the GPU path. Stub its eager imports
    # so this unit test also runs in the lightweight gateway test environment.
    torch = type(sys)("torch")
    torch.float16 = object()
    torch_nn = type(sys)("torch.nn")
    torch_functional = type(sys)("torch.nn.functional")
    torch.nn = torch_nn
    torch_nn.functional = torch_functional
    monkeypatch.setitem(sys.modules, "cv2", type(sys)("cv2"))
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "torch.nn", torch_nn)
    monkeypatch.setitem(sys.modules, "torch.nn.functional", torch_functional)
    kornia = type(sys)("kornia")
    geometry = type(sys)("kornia.geometry")
    calibration = type(sys)("kornia.geometry.calibration")
    calibration.distort_points = lambda *args, **kwargs: None
    kornia.geometry = geometry
    geometry.calibration = calibration
    monkeypatch.setitem(sys.modules, "kornia", kornia)
    monkeypatch.setitem(sys.modules, "kornia.geometry", geometry)
    monkeypatch.setitem(sys.modules, "kornia.geometry.calibration", calibration)
    sys.path.insert(0, str(PACK))
    try:
        spec = importlib.util.spec_from_file_location(
            "image_undistort_054_camera_models", PACK / "und_worker.py"
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


@pytest.mark.parametrize(
    ("model", "params", "expected"),
    [
        ("SIMPLE_PINHOLE", "800 320 240", (800.0, 800.0, 320.0, 240.0, 0.0, 0.0, 0.0, 0.0)),
        ("PINHOLE", "800 810 320 240", (800.0, 810.0, 320.0, 240.0, 0.0, 0.0, 0.0, 0.0)),
        ("SIMPLE_RADIAL", "800 320 240 0.01", (800.0, 800.0, 320.0, 240.0, 0.01, 0.0, 0.0, 0.0)),
        (
            "OPENCV",
            "800 810 320 240 0.01 -0.02 0.003 -0.004",
            (800.0, 810.0, 320.0, 240.0, 0.01, -0.02, 0.003, -0.004),
        ),
        ("RADIAL", "800 320 240 0.01 -0.02", (800.0, 800.0, 320.0, 240.0, 0.01, -0.02, 0.0, 0.0)),
    ],
)
def test_parse_cameras_normalizes_supported_models(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model: str,
    params: str,
    expected: tuple[float, ...],
) -> None:
    worker = _worker(monkeypatch)
    cameras = tmp_path / "cameras.txt"
    cameras.write_text(f"1 {model} 640 480 {params}\n")

    camera = worker.parse_cameras(cameras)[1]

    assert (
        tuple(camera[key] for key in ("fx", "fy", "cx", "cy", "k1", "k2", "p1", "p2")) == expected
    )
    assert camera["source_model"] == model


def test_parse_cameras_explains_unsupported_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = _worker(monkeypatch)
    cameras = tmp_path / "cameras.txt"
    cameras.write_text("1 FULL_OPENCV 640 480 800 810 320 240 0 0 0 0 0 0 0 0\n")

    with pytest.raises(SystemExit, match=r"FULL_OPENCV.*12 parameter\(s\).*Supported models"):
        worker.parse_cameras(cameras)
