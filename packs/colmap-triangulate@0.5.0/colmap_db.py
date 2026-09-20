"""Minimal COLMAP SQLite helpers vendored from the upstream reference implementation.

Only the pieces this pack needs (schema DDL + ``add_camera`` / ``add_image``)
plus the 3.11+ ``frames`` / ``rigs`` / ``frame_data`` repair used to unblock
``point_triangulator`` after a pre-populated DB has been through
``feature_extractor``. Ported (with attribution) from:

* ``SpacetimeGaussians/thirdparty/colmap/pre_colmap.py`` (schema + DB class),
  itself lifted from COLMAP's ``scripts/python/database.py`` (MIT).
* ``SpacetimeGaussians/thirdparty/colmap/pre_colmap.py::repair_colmap_db_frames_if_needed``
  and ``export_manual_rigs_frames_txt_from_database`` (STG-specific 3.12+ repair).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np

MAX_IMAGE_ID = 2**31 - 1

CREATE_CAMERAS_TABLE = """CREATE TABLE IF NOT EXISTS cameras (
    camera_id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    model INTEGER NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    params BLOB,
    prior_focal_length INTEGER NOT NULL)"""

CREATE_IMAGES_TABLE = f"""CREATE TABLE IF NOT EXISTS images (
    image_id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    name TEXT NOT NULL UNIQUE,
    camera_id INTEGER NOT NULL,
    prior_qw REAL,
    prior_qx REAL,
    prior_qy REAL,
    prior_qz REAL,
    prior_tx REAL,
    prior_ty REAL,
    prior_tz REAL,
    CONSTRAINT image_id_check CHECK(image_id >= 0 and image_id < {MAX_IMAGE_ID}),
    FOREIGN KEY(camera_id) REFERENCES cameras(camera_id))
"""

CREATE_KEYPOINTS_TABLE = """CREATE TABLE IF NOT EXISTS keypoints (
    image_id INTEGER PRIMARY KEY NOT NULL,
    rows INTEGER NOT NULL,
    cols INTEGER NOT NULL,
    data BLOB,
    FOREIGN KEY(image_id) REFERENCES images(image_id) ON DELETE CASCADE)"""

CREATE_DESCRIPTORS_TABLE = """CREATE TABLE IF NOT EXISTS descriptors (
    image_id INTEGER PRIMARY KEY NOT NULL,
    rows INTEGER NOT NULL,
    cols INTEGER NOT NULL,
    data BLOB,
    FOREIGN KEY(image_id) REFERENCES images(image_id) ON DELETE CASCADE)"""

CREATE_MATCHES_TABLE = """CREATE TABLE IF NOT EXISTS matches (
    pair_id INTEGER PRIMARY KEY NOT NULL,
    rows INTEGER NOT NULL,
    cols INTEGER NOT NULL,
    data BLOB)"""

CREATE_TWO_VIEW_GEOMETRIES_TABLE = """
CREATE TABLE IF NOT EXISTS two_view_geometries (
    pair_id INTEGER PRIMARY KEY NOT NULL,
    rows INTEGER NOT NULL,
    cols INTEGER NOT NULL,
    data BLOB,
    config INTEGER NOT NULL,
    F BLOB,
    E BLOB,
    H BLOB,
    qvec BLOB,
    tvec BLOB)
"""

CREATE_NAME_INDEX = "CREATE UNIQUE INDEX IF NOT EXISTS index_name ON images(name)"

CREATE_ALL = "; ".join(
    [
        CREATE_CAMERAS_TABLE,
        CREATE_IMAGES_TABLE,
        CREATE_KEYPOINTS_TABLE,
        CREATE_DESCRIPTORS_TABLE,
        CREATE_MATCHES_TABLE,
        CREATE_TWO_VIEW_GEOMETRIES_TABLE,
        CREATE_NAME_INDEX,
    ]
)


# COLMAP camera model integer ids (see colmap/src/base/camera_models.h).
CAMERA_MODEL_OPENCV = 4


def create_empty_db(db_path: Path) -> None:
    """Delete + recreate an empty COLMAP DB with the required schema."""
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(str(db_path))
    try:
        con.executescript(CREATE_ALL)
        con.commit()
    finally:
        con.close()


def add_camera(
    con: sqlite3.Connection,
    model: int,
    width: int,
    height: int,
    params: np.ndarray,
    camera_id: int | None = None,
    prior_focal_length: int = 0,
) -> int:
    params = np.asarray(params, np.float64)
    cur = con.execute(
        "INSERT INTO cameras VALUES (?, ?, ?, ?, ?, ?)",
        (camera_id, int(model), int(width), int(height), params.tobytes(), int(prior_focal_length)),
    )
    return int(cur.lastrowid)


def add_image(
    con: sqlite3.Connection,
    name: str,
    camera_id: int,
    prior_q: np.ndarray,
    prior_t: np.ndarray,
    image_id: int | None = None,
) -> int:
    prior_q = np.asarray(prior_q, np.float64)
    prior_t = np.asarray(prior_t, np.float64)
    cur = con.execute(
        "INSERT INTO images VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            image_id,
            name,
            int(camera_id),
            float(prior_q[0]),
            float(prior_q[1]),
            float(prior_q[2]),
            float(prior_q[3]),
            float(prior_t[0]),
            float(prior_t[1]),
            float(prior_t[2]),
        ),
    )
    return int(cur.lastrowid)


def _resolve_rig_id_for_image(
    image_id: int, camera_id: int, rig_rows: list[tuple[int, int, int]]
) -> int:
    """Map (image_id, camera_id) → rig_id following the STG upstream tiebreak.

    Ported verbatim (semantics) from
    ``SpacetimeGaussians/thirdparty/colmap/pre_colmap.py::_resolve_rig_id_for_image``.
    """
    cam = int(camera_id)
    img = int(image_id)
    by_cam: list[int] = []
    for rid, ref_sid, ref_stype in rig_rows:
        if int(ref_stype) == 0 and int(ref_sid) == cam:
            by_cam.append(int(rid))
    if by_cam:
        if img in by_cam:
            return img
        return by_cam[0]

    rig_ids = {int(r[0]) for r in rig_rows}
    if img in rig_ids:
        return img
    for rid, ref_sid, _ref_stype in rig_rows:
        if int(ref_sid) == img:
            return int(rid)
    if len(rig_rows) == 1:
        return int(rig_rows[0][0])
    raise RuntimeError(
        f"Could not map image_id={image_id} camera_id={camera_id} to a rig; "
        f"have {len(rig_rows)} rigs."
    )


def repair_frames_if_needed(db_path: Path) -> None:
    """Rebuild ``frame_data`` + ``frames`` from current ``images`` + ``rigs``.

    COLMAP 3.12+ requires each ``images`` row to have matching ``frame_data``;
    ``feature_extractor`` may leave the pair inconsistent when the DB was
    pre-populated with per-image cameras. Rebuild-on-every-call semantics
    ported from ``pre_colmap.repair_colmap_db_frames_if_needed``.
    """
    if not db_path.is_file():
        return
    con = sqlite3.connect(str(db_path))
    try:
        cur = con.cursor()
        cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='frames'")
        if cur.fetchone() is None:
            return
        cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='frame_data'")
        if cur.fetchone() is None:
            return
        cur.execute("SELECT image_id, camera_id FROM images ORDER BY image_id")
        img_rows = [(int(r[0]), int(r[1])) for r in cur.fetchall()]
        if not img_rows:
            return
        cur.execute("SELECT rig_id, ref_sensor_id, ref_sensor_type FROM rigs ORDER BY rig_id")
        rig_rows = [(int(r[0]), int(r[1]), int(r[2])) for r in cur.fetchall()]
        if not rig_rows:
            return

        cur.execute("DELETE FROM frame_data")
        cur.execute("DELETE FROM frames")

        for img_id, camera_id in img_rows:
            rig_id = _resolve_rig_id_for_image(img_id, camera_id, rig_rows)
            cur.execute("INSERT INTO frames (frame_id, rig_id) VALUES (?, ?)", (img_id, rig_id))
            cur.execute(
                "INSERT INTO frame_data (frame_id, data_id, sensor_id, sensor_type) "
                "VALUES (?, ?, ?, ?)",
                (img_id, img_id, camera_id, 0),
            )
        con.commit()
    finally:
        con.close()


def _sensor_type_int_to_str(sensor_type: int) -> str:
    return "CAMERA" if int(sensor_type) == 0 else str(int(sensor_type))


def _parse_manual_images_txt_poses(manual_dir: Path) -> dict[int, tuple[float, ...]]:
    images_path = manual_dir / "images.txt"
    if not images_path.is_file():
        return {}
    poses: dict[int, tuple[float, ...]] = {}
    lines = images_path.read_text().splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line or line.startswith("#"):
            i += 1
            continue
        parts = line.split()
        if len(parts) < 10:
            i += 1
            continue
        image_id = int(parts[0])
        qw, qx, qy, qz = (float(x) for x in parts[1:5])
        tx, ty, tz = (float(x) for x in parts[5:8])
        poses[image_id] = (qw, qx, qy, qz, tx, ty, tz)
        i += 2
    return poses


def export_manual_rigs_frames_txt(db_path: Path, manual_dir: Path) -> None:
    """Emit ``manual/rigs.txt`` + ``manual/frames.txt`` matching the DB.

    Ported (semantics) from
    ``pre_colmap.export_manual_rigs_frames_txt_from_database``. Only touches
    the manual dir when the DB has ``rigs`` populated.
    """
    if not db_path.is_file() or not manual_dir.is_dir():
        return

    def _fmt(x: float) -> str:
        return format(float(x), ".17g")

    con = sqlite3.connect(str(db_path))
    try:
        cur = con.cursor()
        cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rigs'")
        if not cur.fetchone():
            return
        cur.execute("SELECT COUNT(*) FROM frames")
        if cur.fetchone()[0] == 0:
            return

        poses = _parse_manual_images_txt_poses(manual_dir)

        cur.execute("SELECT rig_id, ref_sensor_id, ref_sensor_type FROM rigs ORDER BY rig_id")
        rig_rows = cur.fetchall()

        rigs_out = [
            "# Rig calib list with one line of data per calib:\n",
            "#   RIG_ID, NUM_SENSORS, REF_SENSOR_TYPE, REF_SENSOR_ID, "
            "SENSORS[] as (SENSOR_TYPE, SENSOR_ID, HAS_POSE, [QW, QX, QY, QZ, TX, TY, TZ])\n",
            f"# Number of rigs: {len(rig_rows)}\n",
        ]
        for rig_id, ref_sid, ref_stype in rig_rows:
            rtype = _sensor_type_int_to_str(int(ref_stype))
            rigs_out.append(f"{int(rig_id)} 1 {rtype} {int(ref_sid)}\n")
        (manual_dir / "rigs.txt").write_text("".join(rigs_out))

        cur.execute("SELECT frame_id, rig_id FROM frames ORDER BY frame_id")
        frame_rows = cur.fetchall()

        frames_out = [
            "# Frame list with one line of data per frame:\n",
            "#   FRAME_ID, RIG_ID, RIG_FROM_WORLD[QW, QX, QY, QZ, TX, TY, TZ], "
            "NUM_DATA_IDS, DATA_IDS[] as (SENSOR_TYPE, SENSOR_ID, DATA_ID)\n",
            f"# Number of frames: {len(frame_rows)}\n",
        ]
        for frame_id, rig_id in frame_rows:
            fid = int(frame_id)
            rid = int(rig_id)
            if fid in poses:
                qw, qx, qy, qz, tx, ty, tz = poses[fid]
            else:
                qw, qx, qy, qz = 1.0, 0.0, 0.0, 0.0
                tx = ty = tz = 0.0
            cur.execute(
                "SELECT sensor_type, sensor_id, data_id FROM frame_data "
                "WHERE frame_id = ? ORDER BY data_id",
                (fid,),
            )
            fd_rows = cur.fetchall()
            if not fd_rows:
                raise RuntimeError(f"no frame_data for frame_id={fid}")
            parts = [
                str(fid),
                str(rid),
                _fmt(qw),
                _fmt(qx),
                _fmt(qy),
                _fmt(qz),
                _fmt(tx),
                _fmt(ty),
                _fmt(tz),
                str(len(fd_rows)),
            ]
            for stype, sid, did in fd_rows:
                parts.append(_sensor_type_int_to_str(int(stype)))
                parts.append(str(int(sid)))
                parts.append(str(int(did)))
            frames_out.append(" ".join(parts) + "\n")

        (manual_dir / "frames.txt").write_text("".join(frames_out))
    finally:
        con.close()
