"""Regroup camera ordinals must not replace the source rig's camera identity."""

import importlib.util
import sqlite3
from pathlib import Path

import numpy as np
import pytest


@pytest.mark.parametrize("shared_camera", [False, True])
def test_source_keys_and_original_ids_survive_prefill(tmp_path, shared_camera):
    pack = Path(__file__).resolve().parents[1] / "local_packs/colmap-triangulate@0.7.7"
    spec = importlib.util.spec_from_file_location("tri_source_identity", pack / "triangulate.py")
    tri = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tri)
    q, t = np.array([1.0, 0.0, 0.0, 0.0]), np.zeros(3)
    second_cid = 11 if shared_camera else 29
    poses = {"data1": (101, q, t, 11), "data10": (203, q, t, second_cid)}
    cameras = {
        11: ("SIMPLE_RADIAL", 100, 80, [70.0, 50.0, 40.0, 0.0]),
        29: ("SIMPLE_RADIAL", 100, 80, [72.0, 50.0, 40.0, 0.0]),
    }
    # cam_0001's numeric suffix would select data1, which is the wrong view.
    aliases = {"cam_0000.png": "data1", "cam_0001.png": "data10"}
    db = tmp_path / "input.db"
    resolved = tri.prefill_db(db, tmp_path / "manual", list(aliases), cameras, poses, aliases)
    assert resolved == list(aliases.items())
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT image_id, camera_id, name FROM images ORDER BY image_id"
        ).fetchall() == [(101, 11, "cam_0000.png"), (203, second_cid, "cam_0001.png")]
        assert conn.execute("SELECT COUNT(*) FROM cameras").fetchone()[0] == (
            1 if shared_camera else 2
        )


def test_unknown_source_key_does_not_fall_back_to_wrong_numeric_camera(tmp_path):
    pack = Path(__file__).resolve().parents[1] / "local_packs/colmap-triangulate@0.7.7"
    spec = importlib.util.spec_from_file_location("tri_source_missing", pack / "triangulate.py")
    tri = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tri)
    poses = {"data1": (101, np.array([1.0, 0.0, 0.0, 0.0]), np.zeros(3), 11)}
    cameras = {11: ("SIMPLE_RADIAL", 100, 80, [70.0, 50.0, 40.0, 0.0])}
    assert (
        tri.prefill_db(
            tmp_path / "input.db",
            tmp_path / "manual",
            ["cam_0001.png"],
            cameras,
            poses,
            {"cam_0001.png": "absent"},
        )
        == []
    )
