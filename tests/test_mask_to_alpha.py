"""Positional pairing, provenance failures, and exact soft-alpha preservation."""

import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

PACK = Path(__file__).resolve().parents[1] / "community_packs/mask-to-alpha@0.1.0"
spec = importlib.util.spec_from_file_location("mask_to_alpha", PACK / "run.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def fixture(tmp_path):
    images, masks, out = (tmp_path / n for n in ("images", "masks", "out"))
    images.mkdir()
    masks.mkdir()
    frames = []
    for i, (iname, mname) in enumerate([("a.png", "different.png"), ("z.png", "other.png")]):
        rgb = np.full((3, 4, 3), 40 + i, dtype=np.uint8)
        Image.fromarray(rgb).save(images / iname)
        Image.fromarray(np.arange(12, dtype=np.uint8).reshape(3, 4) * 23).save(masks / mname)
        frames.append({"rgb_sha256": hashlib.sha256(rgb.tobytes()).hexdigest()})
    (masks / ".validation.json").write_text(json.dumps({"frames": frames}))
    return images, masks, out


def test_position_soft_alpha_and_rgb(tmp_path):
    images, masks, out = fixture(tmp_path)
    mod.apply_masks(images, masks, out)
    for src, mask, result in zip(
        mod.image_group(images), mod.image_group(masks), mod.image_group(out), strict=True
    ):
        with Image.open(result) as im:
            assert im.mode == "RGBA"
            np.testing.assert_array_equal(np.asarray(im)[..., :3], np.asarray(Image.open(src)))
            np.testing.assert_array_equal(np.asarray(im)[..., 3], np.asarray(Image.open(mask)))
    assert (out / ".hololab-done").exists()


@pytest.mark.parametrize("failure", ["count", "size", "hash", "metadata"])
def test_fail_closed(tmp_path, failure):
    images, masks, out = fixture(tmp_path)
    if failure == "count":
        (masks / "other.png").unlink()
    elif failure == "size":
        Image.new("L", (1, 1)).save(masks / "other.png")
    elif failure == "hash":
        Image.new("RGB", (4, 3)).save(images / "a.png")
    else:
        (masks / ".validation.json").unlink()
    with pytest.raises(ValueError):
        mod.apply_masks(images, masks, out)
    assert not (out / ".hololab-done").exists()
