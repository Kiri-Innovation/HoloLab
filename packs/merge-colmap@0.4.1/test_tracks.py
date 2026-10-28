import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("merge_v041", Path(__file__).with_name("merge.py"))
merge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(merge)


def test_tracks_require_retained_observations(tmp_path):
    images = tmp_path / "images.txt"
    points = tmp_path / "points3D.txt"
    points.write_text("9 0 0 1 255 255 255 0 1 0\n")
    images.write_text("1 1 0 0 0 0 0 0 1 frame.png\n2 3 9\n")
    merge._validate_tracks(images, points)
    images.write_text("1 1 0 0 0 0 0 0 1 frame.png\n\n")
    with pytest.raises(ValueError, match="outside 0 observations"):
        merge._validate_tracks(images, points)


def test_nonreciprocal_track_rejected(tmp_path):
    images = tmp_path / "images.txt"
    points = tmp_path / "points3D.txt"
    images.write_text("1 1 0 0 0 0 0 0 1 frame.png\n2 3 -1\n")
    points.write_text("9 0 0 1 255 255 255 0 1 0\n")
    with pytest.raises(ValueError, match="nonreciprocal"):
        merge._validate_tracks(images, points)
