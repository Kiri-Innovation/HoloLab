"""RGB compatibility and shared-grid RGBA regression on CUDA."""

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("cv2")
torch = pytest.importorskip("torch")
pytest.importorskip("kornia")
ROOT = Path(__file__).resolve().parents[1] / "community_packs"


def worker(version):
    path = ROOT / f"image-undistort@{version}"
    sys.path.insert(0, str(path))
    try:
        spec = importlib.util.spec_from_file_location("und_" + version, path / "und_worker.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m
    finally:
        sys.path.pop(0)


@pytest.mark.parametrize("mode", ["RGB", "L", "RGBA"])
def test_io(tmp_path, mode):
    from PIL import Image

    old, new = worker("0.6.1"), worker("0.6.2")
    im = Image.new(mode, (17, 13))
    src = tmp_path / "input.png"
    im.save(src)
    a = new._read_rgb(src)
    assert a.shape == (13, 17, 4 if mode == "RGBA" else 3)
    new._write_rgb(tmp_path / "new.png", a, 1)
    if mode != "RGBA":
        np.testing.assert_array_equal(a, old._read_rgb(src))
        old._write_rgb(tmp_path / "old.png", a, 1)
        assert (tmp_path / "old.png").read_bytes() == (tmp_path / "new.png").read_bytes()
    else:
        np.testing.assert_array_equal(np.asarray(Image.open(tmp_path / "new.png")), np.asarray(im))


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA required")
@pytest.mark.parametrize("dtype", [torch.float16, torch.float32])
def test_gpu_rgb_compatibility_and_alpha_geometry(dtype):
    old, new = worker("0.6.1"), worker("0.6.2")
    rng = np.random.default_rng(42)
    rgba = rng.integers(0, 256, (2, 80, 120, 4), dtype=np.uint8)
    # Nonidentity grid, including out-of-bounds zero padding.
    y, x = torch.meshgrid(
        torch.linspace(-1.1, 1.1, 72, device="cuda"),
        torch.linspace(-1.1, 1.1, 112, device="cuda"),
        indexing="ij",
    )
    grid = torch.stack([x + 0.04 * y**2, y], dim=-1)[None]
    baseline = old.undistort_batch(rgba[..., :3], grid, compute_dtype=dtype)
    current = new.undistort_batch(rgba[..., :3], grid, compute_dtype=dtype)
    np.testing.assert_array_equal(baseline, current)
    result = new.undistort_batch(rgba, grid, compute_dtype=dtype)
    # Different channel packing can change fp16 rounding by one 8-bit level.
    np.testing.assert_allclose(result[..., :3], baseline, atol=1, rtol=0)
    alpha_reference = old.undistort_batch(
        np.repeat(rgba[..., 3:4], 3, axis=-1), grid, compute_dtype=dtype
    )
    np.testing.assert_allclose(result[..., 3], alpha_reference[..., 0], atol=1, rtol=0)


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA required")
@pytest.mark.parametrize("mode,suffix", [("RGB", ".png"), ("RGB", ".jpg"), ("RGBA", ".png"), ("RGBA", ".tiff")])
def test_shard_png_and_pose_names(tmp_path, mode, suffix):
    from PIL import Image

    old, new = worker("0.6.1"), worker("0.6.2")
    src = tmp_path / "input"
    src.mkdir()
    channels = 3 if mode == "RGB" else 4
    pixels = np.random.default_rng(3).integers(0, 256, (30, 40, channels), dtype=np.uint8)
    Image.fromarray(pixels).save(src / ("source" + suffix))
    cams = tmp_path / "cams"
    cams.mkdir()
    (cams / "cameras.txt").write_text("1 OPENCV 40 30 30 30 20 15 0.01 0 0 0\n")
    (cams / "images.txt").write_text("1 1 0 0 0 0 0 0 1 unrelated-name.png\n\n")

    def run(m, name):
        out = tmp_path / name
        request = dict(
            cams=str(cams),
            images=str(src),
            cams_out=str(out / "cams"),
            images_out=str(out / "images"),
            scratch=str(out / "scratch"),
            blank_pixels=0,
            io_workers=1,
            png_compress=1,
            chunk_size=1,
            dtype="fp16",
        )
        assert m.process_shard(request) == 0
        return out

    result = run(new, "new")
    fname = "p_0000000000000000" + suffix
    with Image.open(result / "images" / fname) as im:
        assert im.mode == mode
    assert fname in (result / "cams/images.txt").read_text()
    if mode == "RGB":
        previous = run(old, "old")
        for relative in [
            "images/" + fname,
            "cams/cameras.txt",
            "cams/images.txt",
            "cams/points3D.txt",
        ]:
            assert (previous / relative).read_bytes() == (result / relative).read_bytes()


@pytest.mark.parametrize("channels", [1, 3, 4])
def test_uint16_decode(tmp_path, channels):
    import cv2

    old, new = worker("0.6.1"), worker("0.6.2")
    shape = (13, 17) if channels == 1 else (13, 17, channels)
    pixels = np.random.default_rng(12).integers(0, 65536, shape, dtype=np.uint16)
    src = tmp_path / "sixteen.png"
    assert cv2.imwrite(str(src), pixels)
    actual = new._read_rgb(src)
    assert actual.dtype == np.uint8
    if channels == 4:
        np.testing.assert_array_equal(actual, cv2.cvtColor((pixels >> 8).astype(np.uint8), cv2.COLOR_BGRA2RGBA))
    else:
        np.testing.assert_array_equal(actual, old._read_rgb(src))


def test_oriented_jpeg_and_xmp_unchanged(tmp_path):
    from PIL import Image

    old, new = worker("0.6.1"), worker("0.6.2")
    pixels = np.random.default_rng(12).integers(0, 256, (13, 17, 3), dtype=np.uint8)
    exif = Image.Exif()
    exif[274] = 6
    src = tmp_path / "oriented.jpg"
    Image.fromarray(pixels).save(src, exif=exif)
    assert new._read_rgb(src).shape == old._read_rgb(src).shape == (17, 13, 3)
    payload = b"http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta>test</x:xmpmeta>"
    data = src.read_bytes()
    src.write_bytes(data[:2] + b"\xff\xe1" + (len(payload) + 2).to_bytes(2, "big") + payload + data[2:])
    actual = new._read_rgb(src)
    np.testing.assert_array_equal(actual, old._read_rgb(src))
    old._write_rgb(tmp_path / "old.jpg", actual, 1, src)
    new._write_rgb(tmp_path / "new.jpg", actual, 1, src)
    assert (tmp_path / "old.jpg").read_bytes() == (tmp_path / "new.jpg").read_bytes()
    assert payload in (tmp_path / "new.jpg").read_bytes()
