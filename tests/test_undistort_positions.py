import importlib.util
from pathlib import Path

import pytest

from hololab.array_positions import decode_position, encode_position

PACK = Path(__file__).resolve().parents[1] / "community_packs/image-undistort@0.6.0"


def load(monkeypatch, file="position_prior.py"):
    monkeypatch.syspath_prepend(str(PACK))
    spec = importlib.util.spec_from_file_location("und_positions_" + file, PACK / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def setup_model(tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    (model / "cameras.txt").write_text("77 PINHOLE 20 10 10 10 10 5\n")
    (model / "images.txt").write_text(
        "9 1 0 0 0 9 0 0 77 original_A.jpg\n0 0 3\n2 1 0 0 0 2 0 0 77 unrelated_Z.jpg\n0 0 3\n"
    )
    (model / "points3D.txt").write_text("3 0 0 0 1 1 1 0 2 0 9 0\n")
    (model / "frames.txt").write_text("1 1 1 0 0 0 0 0 0 2 CAMERA 77 2 CAMERA 77 9\n")
    return model


def test_unrelated_names_zip_and_references_stay_valid(tmp_path, monkeypatch):
    mod = load(monkeypatch)
    model = setup_model(tmp_path)
    source = tmp_path / "input"
    source.mkdir()
    target = tmp_path / "unrelated-parent"
    target.mkdir()
    for name, value in [("z.png", b"z"), ("a.png", b"a")]:
        (target / name).write_bytes(value)
        (source / name).symlink_to(target / name)
    staged = mod.stage_images(source, tmp_path / "staged")
    poses, _ = mod.stage_sparse_prior(model, tmp_path / "prior", staged)
    assert [int(row[0]) for row, _ in poses] == [2, 9]
    assert [decode_position(Path(row[9]).stem) for row, _ in poses] == [0, 1]
    assert [p.read_bytes() for p in staged] == [b"a", b"z"]
    assert (tmp_path / "prior/points3D.txt").read_bytes() == (model / "points3D.txt").read_bytes()
    output = tmp_path / "output"
    output.mkdir()
    mod.publish_poses(output, poses, model)
    assert (output / "points3D.txt").read_text() == ""
    assert (output / "frames.txt").read_text().split()[-1] == "9"
    assert "0 0 3" not in (output / "images.txt").read_text()


def test_lengths_fail_without_filter(tmp_path, monkeypatch):
    mod = load(monkeypatch)
    with pytest.raises(ValueError, match="length mismatch"):
        mod.stage_sparse_prior(setup_model(tmp_path), tmp_path / "out", [Path("different.png")])
    assert not (tmp_path / "out").exists()


def test_old_helpers_absent_and_daemon_hash_complete(monkeypatch):
    assert not (PACK / "sfm_key.py").exists()
    worker = (PACK / "und_worker.py").read_text()
    for forbidden in [
        "source_keys",
        "cam_key",
        "numeric_suffix",
        "_resolve_file_for_sfm_name",
        "_build_cam_file_map",
    ]:
        assert forbidden not in worker
    client = load(monkeypatch, "undistort.py")
    assert {
        "position_prior.py",
        "position_model.py",
        "array_positions.py",
        "jpeg_metadata.py",
    } <= set(client._HASH_INPUTS)
    assert len(client._manifest_hash()) == 12
    assert encode_position(1) == "p_0000000000000001"


def test_cpu_mocked_worker_keeps_unused_rig_sensor_camera(tmp_path, monkeypatch):
    import sys
    from types import ModuleType, SimpleNamespace

    import numpy as np

    modules = {
        name: ModuleType(name)
        for name in [
            "cv2",
            "torch",
            "torch.nn",
            "torch.nn.functional",
            "kornia",
            "kornia.geometry",
            "kornia.geometry.calibration",
        ]
    }
    modules["torch"].float16 = object()
    modules["torch"].float32 = object()
    modules["torch"].cuda = SimpleNamespace(synchronize=lambda: None)
    modules["kornia.geometry.calibration"].distort_points = lambda *a: None
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    worker = load(monkeypatch, "und_worker.py")
    model = setup_model(tmp_path)
    with (model / "cameras.txt").open("a") as f:
        f.write("99 PINHOLE 20 10 10 10 10 5\n")
    (model / "rigs.txt").write_text("1 2 CAMERA 77 CAMERA 99 0\n")
    images = tmp_path / "inputs"
    images.mkdir()
    for i in range(2):
        (images / f"random{i}.png").write_bytes(b"image")
    monkeypatch.setattr(worker, "_read_rgb", lambda p: np.zeros((10, 20, 3), dtype=np.uint8))
    monkeypatch.setattr(worker, "_write_rgb", lambda p, *a, **k: p.write_bytes(b"image"))
    monkeypatch.setattr(worker, "_ensure_cuda_warm", lambda: None)
    monkeypatch.setattr(worker, "_build_grid", lambda *a: None)
    monkeypatch.setattr(worker, "undistort_batch", lambda imgs, *a, **k: imgs)
    monkeypatch.setattr(worker, "compute_new_pinhole", lambda cam, **kw: dict(cam))
    request = {
        "cams": str(model),
        "images": str(images),
        "cams_out": str(tmp_path / "out_cams"),
        "images_out": str(tmp_path / "out_images"),
        "scratch": str(tmp_path / "scratch"),
        "blank_pixels": 0,
        "io_workers": 1,
        "png_compress": 1,
        "dtype": "fp32",
    }
    assert worker.process_shard(request) == 0
    assert {
        line.split()[0]
        for line in (tmp_path / "out_cams/cameras.txt").read_text().splitlines()
        if line and not line.startswith("#")
    } == {"77", "99"}
