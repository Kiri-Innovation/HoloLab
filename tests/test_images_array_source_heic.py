"""HEIC source conversion and compatibility with the builtin source contract."""

import subprocess
import sys
from pathlib import Path

import pytest

from hololab.manifest import load_manifest

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "community_packs/images-array-source@0.1.0"
BUILTIN = ROOT / "hololab/builtin_packs/images-array-source@0.1.0"


def run(source, output, pack=PACK):
    return subprocess.run(
        [
            sys.executable,
            str(pack / "run.py"),
            "--frames-path",
            str(source),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
    )


def test_manifest_contract():
    new, _ = load_manifest(PACK / "manifest.yaml")
    old, _ = load_manifest(BUILTIN / "manifest.yaml")
    assert new.runtime.env == "kiri"
    assert not new.runtime.gpu.required
    assert new.outputs["frames"].tags == old.outputs["frames"].tags
    assert new.outputs["frames"].dim_labels == old.outputs["frames"].dim_labels
    assert new.outputs["frames"].storage == old.outputs["frames"].storage
    assert new.outputs["frames"].arrayed == old.outputs["frames"].arrayed
    assert "pillow-heif==1.3.0" in new.requires.packages


def test_non_heic_identical_to_builtin(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for name in ["02.png", "01.JPG", "03.webp", ".hidden.png", "ignored.txt"]:
        (source / name).write_bytes(b"unchanged even without decoding")
    (source / "subdir.png").mkdir()
    outputs = [tmp_path / "old", tmp_path / "new"]
    for pack, output in zip([BUILTIN, PACK], outputs, strict=True):
        assert run(source, output, pack).returncode == 0
    assert sorted(p.name for p in outputs[0].iterdir()) == ["01.JPG", "02.png", "03.webp"]
    for old in outputs[0].iterdir():
        new = outputs[1] / old.name
        assert old.is_symlink() and new.is_symlink()
        assert old.readlink() == new.readlink()


@pytest.mark.parametrize("mode", ["RGB", "RGBA"])
def test_heic_and_mixed_pixels_order_alpha(tmp_path, mode):
    heif = pytest.importorskip("pillow_heif")
    from PIL import Image

    source = tmp_path / "source"
    source.mkdir()
    original = Image.new(mode, (32, 24), (34, 67, 123, 150) if mode == "RGBA" else (34, 67, 123))
    heif.from_pillow(original).save(source / "01.HEIC", quality=100)
    original.save(source / "02.png")
    output = tmp_path / "out"
    result = run(source, output)
    assert result.returncode == 0, result.stderr
    assert "converted 1 HEIC" in result.stdout
    assert sorted(p.name for p in output.iterdir()) == ["01.png", "02.png"]
    assert not (output / "01.png").is_symlink()
    assert (output / "02.png").is_symlink()
    with (
        heif.open_heif(source / "01.HEIC", convert_hdr_to_8bit=False).to_pillow() as decoded,
        Image.open(output / "01.png") as png,
    ):
        assert png.format == "PNG"
        assert png.size == decoded.size == (32, 24)
        assert png.mode == decoded.mode == mode
        assert png.tobytes() == decoded.tobytes()


@pytest.mark.parametrize("other", ["a.png", "a.jpg"])
def test_suffix_collision_and_reordering_keep_all_frames(tmp_path, other):
    heif = pytest.importorskip("pillow_heif")
    from PIL import Image

    source = tmp_path / "source"
    source.mkdir()
    heif.from_pillow(Image.new("RGB", (8, 8))).save(source / "a.heic")
    (source / other).write_bytes(b"linked unchanged")
    result = run(source, tmp_path / "out")
    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["a.heic.png", other]


def test_source_cannot_be_output(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    image = source / "01.png"
    image.write_bytes(b"original")
    assert run(source, source).returncode == 2
    assert image.read_bytes() == b"original"
    assert not image.is_symlink()


def test_invalid_heic_fails_clearly(tmp_path):
    pytest.importorskip("pillow_heif")
    source = tmp_path / "source"
    source.mkdir()
    (source / "bad.heic").write_bytes(b"not heic")
    result = run(source, tmp_path / "out")
    assert result.returncode == 2
    assert "failed to process" in result.stderr


def test_all_png_remain_real_image_symlinks(tmp_path):
    from PIL import Image

    source = tmp_path / "source"
    source.mkdir()
    for name in ["02.png", "01.png"]:
        Image.new("RGB", (17, 19), (42, 84, 126)).save(source / name)
    output = tmp_path / "out"
    result = run(source, output)
    assert result.returncode == 0, result.stderr
    assert "converted 0 HEIC" in result.stdout
    assert sorted(p.name for p in output.iterdir()) == ["01.png", "02.png"]
    for target in output.iterdir():
        assert target.is_symlink()
        assert target.readlink() == source / target.name
        with Image.open(target) as image:
            assert image.size == (17, 19)
            assert image.getpixel((0, 0)) == (42, 84, 126)


def test_host_rejects_shell_entrypoint():
    from pydantic import ValidationError

    old, _ = load_manifest(BUILTIN / "manifest.yaml")
    data = old.model_dump()
    data["exec"] = {"shell": "ffmpeg -version"}
    with pytest.raises(ValidationError, match="host runtime is reserved"):
        type(old).model_validate(data)
