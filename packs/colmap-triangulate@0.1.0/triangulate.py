#!/usr/bin/env python3
"""Triangulate 3D points against known camera poses from an upstream SfM.

The tricky bit is COLMAP 3.11+ rig-consistency. ``point_triangulator`` calls
``Reconstruction::Load(database_cache)``; for each frame in the fresh DB, it
looks up the prior's frame with the same ``frame_id`` and asserts they share
the same ``RigId``. Under the default one-rig-per-camera / one-frame-per-image
convention (``reconstruction_io_utils.cc``), that reduces to *"for each DB
image id X, the prior's image id X must reference the same camera_id."*
SQLite auto-increments image_id and camera_id in **insertion order**, and the
extractor's reader threads insert in a non-deterministic order — so
mechanically copying the SfM's ``cameras.txt``/``images.txt`` into the prior
almost always fails on shards other than the SfM's own (and even on the SfM's
own by luck alone).

Fix — the same pattern ``rig_triangulate.py`` uses for its rig-calibration
sibling: build the fresh DB first, query its assigned (image_id, name,
camera_id), then synthesise a prior model whose IDs are aligned with the DB
by construction, filling in intrinsics and poses looked up from the SfM
output via the per-camera key extracted from the resolved image path.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path


def cam_key(image_name: str) -> str:
    """Per-camera key from a COLMAP-stored image name.

    STG's ``regroup-by-frame`` upstream puts the shard's images as symlinks
    of shape ``<by_frame_dir>/frames/<cam>.<ext>`` pointing at the per-cam
    ``<extractor_out>/frame_sequence/<cam>/frames/<frame>.<ext>`` file.
    COLMAP resolves the symlink and stores the relative resolved path — so
    the last three components are ``<cam>/frames/<basename>`` and the
    grandparent name IS the cam key. Both the SfM run (on element 0 of
    ``regroup-by-frame``) and every triangulate shard follow the same
    convention, so this key extracts the same string on both sides.
    """
    return Path(image_name).parent.parent.name


def parse_cameras_txt(path: Path) -> dict[int, str]:
    """camera_id -> the ``MODEL WIDTH HEIGHT PARAMS...`` tail of the line."""
    out: dict[int, str] = {}
    for ln in path.read_text().splitlines():
        if not ln or ln.startswith("#"):
            continue
        parts = ln.split()
        out[int(parts[0])] = " ".join(parts[1:])
    return out


def parse_images_txt(path: Path) -> list[tuple[int, str, int, str]]:
    """Return ``(image_id, pose7, camera_id, name)`` per image entry.

    ``pose7`` is the seven-number ``QW QX QY QZ TX TY TZ`` prefix. Skips
    the alternating 2D-points lines COLMAP writes after each image header.
    """
    out: list[tuple[int, str, int, str]] = []
    lines = path.read_text().splitlines()
    it = iter(lines)
    for ln in it:
        if not ln or ln.startswith("#"):
            continue
        parts = ln.split()
        image_id = int(parts[0])
        pose7 = " ".join(parts[1:8])
        camera_id = int(parts[8])
        name = parts[9]
        out.append((image_id, pose7, camera_id, name))
        # Consume the paired 2D-points line (may be empty).
        try:
            next(it)
        except StopIteration:
            break
    return out


def run(cmd: list, step: str) -> None:
    print(f"[colmap-triangulate] {step}", flush=True)
    subprocess.run([str(x) for x in cmd], check=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--cams",
        type=Path,
        required=True,
        help="Upstream colmap-cams handle (contains cameras.txt + images.txt).",
    )
    ap.add_argument(
        "--image-path",
        type=Path,
        required=True,
        help="This shard's frames/ directory (from regroup-by-frame).",
    )
    ap.add_argument(
        "--output",
        type=Path,
        required=True,
        help="colmap-points output — will contain points3D.txt.",
    )
    ap.add_argument(
        "--scratch",
        type=Path,
        required=True,
        help="Scratch dir for the intermediate DB, prior, and sparse model.",
    )
    ap.add_argument("--use-gpu", type=int, default=1)
    ap.add_argument("--max-image-size", type=int, default=2400)
    ap.add_argument("--max-num-features", type=int, default=4096)
    ap.add_argument("--filter-max-reproj-error", type=float, default=4.0)
    args = ap.parse_args()

    cams_txt = args.cams / "cameras.txt"
    imgs_txt = args.cams / "images.txt"
    if not cams_txt.is_file() or not imgs_txt.is_file():
        print(
            f"ERROR: cams input missing cameras.txt / images.txt under {args.cams}", file=sys.stderr
        )
        return 2
    if not args.image_path.is_dir():
        print(f"ERROR: image path is not a directory: {args.image_path}", file=sys.stderr)
        return 2

    args.output.mkdir(parents=True, exist_ok=True)
    args.scratch.mkdir(parents=True, exist_ok=True)
    db = args.scratch / "database.db"
    prior = args.scratch / "prior"
    sparse = args.scratch / "sparse"
    if db.exists():
        db.unlink()
    for d in (prior, sparse):
        if d.exists():
            shutil.rmtree(d)
    prior.mkdir(parents=True)
    sparse.mkdir(parents=True)

    sfm_cams = parse_cameras_txt(cams_txt)
    sfm_by_key: dict[str, dict[str, str]] = {}
    for _iid, pose7, cid, name in parse_images_txt(imgs_txt):
        key = cam_key(name)
        if cid not in sfm_cams:
            print(
                f"ERROR: SfM images.txt references unknown camera_id {cid} (image {name!r})",
                file=sys.stderr,
            )
            return 3
        sfm_by_key[key] = {"pose7": pose7, "cam_line_tail": sfm_cams[cid]}
    if not sfm_by_key:
        print("ERROR: no image entries parsed from SfM images.txt", file=sys.stderr)
        return 3

    run(
        [
            "colmap",
            "feature_extractor",
            "--database_path",
            db,
            "--image_path",
            args.image_path,
            "--SiftExtraction.max_image_size",
            args.max_image_size,
            "--SiftExtraction.max_num_features",
            args.max_num_features,
            "--FeatureExtraction.use_gpu",
            args.use_gpu,
        ],
        "feature_extractor",
    )

    con = sqlite3.connect(db)
    db_rows = list(con.execute("SELECT image_id, name, camera_id FROM images ORDER BY image_id"))
    con.close()
    if not db_rows:
        print("ERROR: fresh DB has no images after feature_extractor", file=sys.stderr)
        return 3

    # Build the prior aligned with the fresh DB's IDs.
    cam_lines: dict[int, str] = {}
    img_lines: list[str] = []
    for image_id, name, camera_id in db_rows:
        key = cam_key(name)
        entry = sfm_by_key.get(key)
        if entry is None:
            print(
                f"ERROR: no SfM entry for cam key {key!r} extracted from "
                f"DB image name {name!r}. Available SfM keys: "
                f"{sorted(sfm_by_key)}",
                file=sys.stderr,
            )
            return 4
        cam_lines[camera_id] = f"{camera_id} {entry['cam_line_tail']}"
        img_lines.append(f"{image_id} {entry['pose7']} {camera_id} {name}\n\n")

    (prior / "cameras.txt").write_text("\n".join(cam_lines.values()) + "\n")
    (prior / "images.txt").write_text("".join(img_lines))
    (prior / "points3D.txt").write_text("")

    run(
        [
            "colmap",
            "exhaustive_matcher",
            "--database_path",
            db,
            "--FeatureMatching.use_gpu",
            args.use_gpu,
            "--FeatureMatching.guided_matching",
            "1",
        ],
        "exhaustive_matcher",
    )

    run(
        [
            "colmap",
            "point_triangulator",
            "--database_path",
            db,
            "--image_path",
            args.image_path,
            "--input_path",
            prior,
            "--output_path",
            sparse,
            "--clear_points",
            "1",
            "--Mapper.num_threads",
            "1",
            "--Mapper.ba_refine_focal_length",
            "0",
            "--Mapper.ba_refine_principal_point",
            "0",
            "--Mapper.ba_refine_extra_params",
            "0",
            "--Mapper.filter_max_reproj_error",
            args.filter_max_reproj_error,
        ],
        "point_triangulator (frozen intrinsics)",
    )

    run(
        [
            "colmap",
            "model_converter",
            "--input_path",
            sparse,
            "--output_path",
            args.output,
            "--output_type",
            "TXT",
        ],
        "model_converter → TXT",
    )

    # cams came from upstream; downstream keeps reading them from ``cams``.
    # rigs.txt / frames.txt are byproducts of model_converter — the pack's
    # contract is *points only*, so drop them too.
    for extra in ("cameras.txt", "images.txt", "rigs.txt", "frames.txt"):
        p = args.output / extra
        if p.exists():
            p.unlink()

    pts3d = args.output / "points3D.txt"
    if not pts3d.exists() or pts3d.stat().st_size == 0:
        print("ERROR: no points3D.txt produced", file=sys.stderr)
        return 4
    print(f"[colmap-triangulate] done → {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
