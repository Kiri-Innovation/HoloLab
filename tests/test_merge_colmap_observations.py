"""Name rewrites must not invalidate point-cloud track indices."""

import importlib.util
import sys
from pathlib import Path


def test_mode_b_retains_observation_rows(tmp_path, monkeypatch):
    pack = Path(__file__).resolve().parents[1] / "packs" / "merge-colmap@0.4.1"
    spec = importlib.util.spec_from_file_location("merge_observations", pack / "merge.py")
    merge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(merge)
    cams, points, images, out = [tmp_path / name for name in ("cams", "points", "images", "out")]
    for folder in (cams, points, images):
        folder.mkdir()
    (cams / "cameras.txt").write_text("1 PINHOLE 8 8 4 4 4 4\n")
    observation = "2 3 9 4 5 -1"
    (cams / "images.txt").write_text(f"1 1 0 0 0 0 0 0 1 frame.png\n{observation}\n")
    (points / "points3D.txt").write_text("9 0 0 1 255 255 255 0 1 0\n")
    (images / "frame.png").write_bytes(b"fixture")
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
    calls = []
    monkeypatch.setattr(
        merge.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs))
    )
    assert merge.main() == 0
    assert (out / "sparse/0/images.txt").read_text().splitlines()[1] == observation
    assert (out / "sparse/0/points3D.txt").read_bytes() == (points / "points3D.txt").read_bytes()
    assert calls[0][1]["check"] is True
