"""Fail-closed checks for missing frames, invalid masks, and ambiguous video inputs."""

import importlib.util
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

spec = importlib.util.spec_from_file_location("sam_mask_run", Path(__file__).with_name("run.py"))
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)


class FakePredictor:
    def __init__(self, indices):
        self.indices = indices

    def handle_stream_request(self, request):
        for i in self.indices:
            yield {
                "frame_index": i,
                "outputs": {"out_binary_masks": np.ones((1, 2, 3), dtype=bool)},
            }


@pytest.mark.parametrize("indices", [[0], [0, 0], [0, 2]])
def test_frame_coverage_fails(indices):
    checked = run.CheckedPredictor(FakePredictor(indices), 2, 2, 3)
    with pytest.raises(ValueError):
        list(checked.handle_stream_request({}))


def test_frame_coverage_complete():
    checked = run.CheckedPredictor(FakePredictor([0, 1]), 2, 2, 3)
    assert len(list(checked.handle_stream_request({}))) == 2


@pytest.mark.parametrize(
    "pixels",
    [
        np.zeros((10, 10), np.uint8),
        np.full((10, 10), 255, np.uint8),
        np.full((10, 10), 127, np.uint8),
    ],
)
def test_invalid_mask_fails(tmp_path, pixels):
    path = tmp_path / "mask.png"
    Image.fromarray(pixels).save(path)
    with pytest.raises(ValueError):
        run.validate_mask(path, (10, 10))


def test_valid_mask_and_size(tmp_path):
    path = tmp_path / "mask.png"
    pixels = np.zeros((10, 10), np.uint8)
    pixels[:5] = 255
    Image.fromarray(pixels).save(path)
    assert run.validate_mask(path, (10, 10)) == 0.5
    with pytest.raises(ValueError):
        run.validate_mask(path, (20, 10))


def test_ambiguous_video_fails(tmp_path):
    (tmp_path / "a.mp4").touch()
    (tmp_path / "b.mp4").touch()
    with pytest.raises(ValueError):
        run.single_video(tmp_path)


def test_small_person_is_valid(tmp_path):
    path = tmp_path / "small-person.png"
    pixels = np.zeros((100, 100), np.uint8)
    pixels[10:30, 10:30] = 255
    Image.fromarray(pixels).save(path)
    assert run.validate_mask(path, (100, 100)) == 0.04
