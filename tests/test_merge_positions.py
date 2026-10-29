import importlib.util
import sys
from pathlib import Path

import pytest

from hololab.array_positions import decode_position, encode_position

PACK = Path(__file__).resolve().parents[1] / "community_packs/merge-colmap@0.5.0"


def load(monkeypatch):
    monkeypatch.syspath_prepend(str(PACK))
    spec = importlib.util.spec_from_file_location("merge_positions", PACK / "merge.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fixture(tmp_path, monkeypatch, tri_ids=(2, 9), count=2):
    mod = load(monkeypatch)
    cams, points, images, out = [tmp_path / name for name in ["cams", "points", "images", "out"]]
    for root in [cams, points, images]:
        root.mkdir()
    (cams / "cameras.txt").write_text("77 PINHOLE 20 10 10 10 10 5\n")
    (points / "cameras.txt").write_text("4 PINHOLE 20 10 20 20 10 5\n")
    (cams / "images.txt").write_text(
        "2 1 0 0 0 0 0 0 77 sourceZ.jpg\n\n9 1 0 0 0 0 0 0 77 sourceA.jpg\n\n"
    )
    (points / "images.txt").write_text(
        "".join(
            f"{iid} 1 0 0 0 0 0 0 4 unrelated{i}.png\n" + ("4 6 3\n" if i == 0 else "\n")
            for i, iid in enumerate(tri_ids)
        )
    )
    (points / "points3D.txt").write_text(f"3 0 0 0 1 1 1 0 {tri_ids[0]} 0\n")
    for i in range(count):
        (images / f"other_{i}.png").write_text(str(i))
    (cams / "rigs.txt").write_text("1 1 CAMERA 77\n")
    (cams / "frames.txt").write_text("1 1 1 0 0 0 0 0 0 2 CAMERA 77 2 CAMERA 77 9\n")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "merge",
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

    def remap(obs, source, target):
        calls.append((source, target))
        return "2 3 3" if obs else ""

    monkeypatch.setattr(mod, "_remap_observations", remap)
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: None)
    return mod, cams, points, images, out, calls


def test_unrelated_names_and_camera_ids_use_positions_and_own_tables(tmp_path, monkeypatch):
    mod, _cams, _points, _images, out, calls = fixture(tmp_path, monkeypatch)
    assert mod.main() == 0
    rows = mod.read_images(out / "sparse/0/images.txt")
    assert [decode_position(Path(row[9]).stem) for row, _ in rows] == [0, 1]
    assert [row[8] for row, _ in rows] == ["1", "1"]
    assert calls[0][0] == ("PINHOLE", [20.0, 20.0, 10.0, 5.0])
    assert calls[0][1] == ("PINHOLE", [10.0, 10.0, 10.0, 5.0])
    assert [p.read_text() for p in sorted((out / "images").iterdir())] == ["0", "1"]
    assert rows[1][1] == ""  # zero-point pose retained
    assert (out / "sparse/0/frames.txt").read_text().split()[-6:] == [
        "CAMERA",
        "1",
        "2",
        "CAMERA",
        "1",
        "9",
    ]
    assert (out / "sparse/0/rigs.txt").read_text().split()[-1] == "1"
    mod._validate_tracks(out / "sparse/0/images.txt", out / "sparse/0/points3D.txt")


@pytest.mark.parametrize("tri_ids", [(2,), (2, 10), (2, 9, 12)])
def test_strict_image_id_set_equality_unchanged(tmp_path, monkeypatch, tri_ids):
    mod, *_ = fixture(tmp_path, monkeypatch, tri_ids=tri_ids)
    with pytest.raises(ValueError, match="must match the camera bundle image IDs"):
        mod.main()


def test_image_lengths_must_equal(tmp_path, monkeypatch):
    mod, *_ = fixture(tmp_path, monkeypatch, count=1)
    with pytest.raises(ValueError, match="length mismatch"):
        mod.main()


def test_invalid_tracks_fail_instead_of_cleanup(tmp_path, monkeypatch):
    mod, _cams, points, *_ = fixture(tmp_path, monkeypatch)
    (points / "points3D.txt").write_text("3 0 0 0 1 1 1 0 999 0\n")
    with pytest.raises(ValueError, match="track references absent"):
        mod.main()


def test_no_name_helpers_and_exact_strict_guard_present():
    assert not (PACK / "sfm_key.py").exists()
    text = (PACK / "merge.py").read_text()
    for name in ["cam_key", "numeric_suffix", "_resolve_image_for_name", "_build_cam_file_map"]:
        assert name not in text
    assert "if set(tri_by_id) != valid_image_ids:" in text
    assert "encode_position(position)" in text
    assert encode_position(0) == "p_0000000000000000"
