import importlib.util
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from hololab.array_positions import decode_position, encode_position

PACK = Path(__file__).resolve().parents[1] / "community_packs/colmap-triangulate@0.8.0"


def load(monkeypatch):
    monkeypatch.syspath_prepend(str(PACK))
    spec = importlib.util.spec_from_file_location("triangulate_positions", PACK / "triangulate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def poses():
    return [
        (2, np.array([1, 0, 0, 0]), np.zeros(3), 77, "unrelated_z.jpg"),
        (9, np.array([1, 0, 0, 0]), np.ones(3), 77, "other_a.png"),
    ]


def test_positional_prefill_shared_intrinsics_and_references(tmp_path, monkeypatch):
    mod = load(monkeypatch)
    names = [encode_position(i) + ".png" for i in range(2)]
    result = mod.prefill_db(
        tmp_path / "db",
        tmp_path / "manual",
        names,
        {77: ("PINHOLE", 20, 10, [10, 10, 10, 5])},
        poses(),
    )
    assert result == list(zip(names, [2, 9], strict=True))
    with sqlite3.connect(tmp_path / "db") as con:
        assert con.execute("select camera_id from cameras").fetchall() == [(77,)]
        assert con.execute(
            "select image_id,camera_id,name from images order by image_id"
        ).fetchall() == [(2, 77, names[0]), (9, 77, names[1])]
    rows = mod.parse_images_txt(tmp_path / "manual/images.txt")
    assert [decode_position(Path(row[4]).stem) for row in rows] == [0, 1]
    mod.validate_manual_references(tmp_path / "db", tmp_path / "manual", [])


def test_length_mismatch_does_not_create_db(tmp_path, monkeypatch):
    mod = load(monkeypatch)
    with pytest.raises(ValueError, match="length mismatch"):
        mod.prefill_db(
            tmp_path / "db",
            tmp_path / "model",
            ["one.png"],
            {77: ("PINHOLE", 20, 10, [10, 10, 10, 5])},
            poses(),
        )
    assert not (tmp_path / "db").exists()


def test_zero_point_pose_retained_and_bad_track_rejected(tmp_path, monkeypatch):
    mod = load(monkeypatch)
    names = [encode_position(i) + ".png" for i in range(2)]
    mod.prefill_db(
        tmp_path / "db",
        tmp_path / "prior",
        names,
        {77: ("PINHOLE", 20, 10, [10, 10, 10, 5])},
        poses(),
    )
    output = tmp_path / "tri"
    output.mkdir()
    (output / "images.txt").write_text(f"2 1 0 0 0 0 0 0 77 {names[0]}\n\n")
    (output / "cameras.txt").write_text("77 PINHOLE 20 10 10 10 10 5\n")
    (output / "points3D.txt").write_text("")
    mod.complete_output_model(tmp_path / "prior", output)
    assert [p[0] for p in mod.parse_images_txt(output / "images.txt")] == [2, 9]
    (output / "points3D.txt").write_text("3 0 0 0 1 1 1 0 999 0\n")
    with pytest.raises(ValueError, match="track"):
        mod.complete_output_model(tmp_path / "prior", output)


def test_no_matching_helper_or_symlink_identity():
    assert not (PACK / "sfm_key.py").exists()
    text = (PACK / "triangulate.py").read_text()
    for forbidden in [
        "sfm_key",
        "cam_key",
        "numeric_suffix",
        "source_keys",
        "resolve_sfm_key",
        "missing_keys",
        "sfm_pose_by_key",
    ]:
        assert forbidden not in text
    assert (PACK / "colmap_db.py").read_bytes() == (
        PACK.with_name("colmap-triangulate@0.7.6") / "colmap_db.py"
    ).read_bytes()
