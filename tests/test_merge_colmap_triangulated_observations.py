import importlib.util
import sys
from pathlib import Path

import pytest


def test_triangulated_tracks_replace_stale_seed_observations(tmp_path, monkeypatch):
    pack = Path(__file__).resolve().parents[1] / "packs" / "merge-colmap@0.4.2"
    sys.path.insert(0, str(pack))
    spec = importlib.util.spec_from_file_location("merge_v042", pack / "merge.py")
    merge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(merge)
    cams, points, images, out = [tmp_path / n for n in ("cams", "points", "images", "out")]
    for p in (cams, points, images):
        p.mkdir()
    pose = "1 1 0 0 0 0 0 0 1 cam_0000.png\n"
    (cams / "cameras.txt").write_text("1 PINHOLE 8 8 4 4 4 4\n")
    (cams / "images.txt").write_text(pose + "2 3 123\n")
    (points / "points3D.txt").write_text("9 0 0 1 255 255 255 0 1 0\n")
    (points / "images.txt").write_text(pose + "4 5 9\n")
    (images / "cam_0000.png").write_bytes(b"fixture")
    with pytest.raises(ValueError, match="nonreciprocal"):
        merge._validate_tracks(cams / "images.txt", points / "points3D.txt")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "merge.py",
            "--cams",
            str(cams),
            "--points",
            str(points),
            "--images",
            str(images),
            "--out",
            str(out),
        ],
    )
    monkeypatch.setattr(merge.subprocess, "run", lambda *a, **k: None)
    assert merge.main() == 0
    assert (out / "sparse/0/images.txt").read_text().splitlines()[1] == "4 5 9"
    merge._validate_tracks(out / "sparse/0/images.txt", out / "sparse/0/points3D.txt")
