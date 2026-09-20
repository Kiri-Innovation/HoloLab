#!/usr/bin/env python3
"""Triangulate against already-undistorted PINHOLE poses (no inline undistort).

Slimmed sibling of colmap-triangulate@0.2.0. Inputs are the typed pair
``colmap-cameras-txt`` (PINHOLE, produced by image-undistort) +
``colmap-images-txt`` (poses, produced by colmap-split) + a
``frame_sequence`` of undistorted images. The pipeline shrinks to:

    hardlink images into flat scratch/input
    -> feature_extractor (single_camera=1, PINHOLE)
    -> rewrite prior IDs to match fresh DB
    -> exhaustive_matcher
    -> point_triangulator (BA global tol 1e-6)
    -> model_converter → TXT
    -> symlink undistorted images into output/images/

Output shape (``colmap-frame``: sparse/0/ + images/) is byte-for-byte
compatible with @0.2.0 so colmap-assemble consumes it unchanged.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path


def cam_key(image_name: str) -> str:
    """Per-camera key from a COLMAP-stored image name.

    Contract (no filename-prefix assumption — key comes from *directory
    structure*, not string parsing):

    * Primary path: flat undistorted layout from ``image-undistort``
      (``<shard>/frames/<cam>.png``) — key = file stem.
    * Legacy tolerance: resolved-symlink shape ``.../<cam>/frames/<file>``
      (per-camera-per-frame from ``regroup-by-frame@0.1.0``) — grandparent
      name is the cam key. Kept so older snapshots keep replaying.

    No dependency on ``frame_`` / ``camera_`` / any other prefix — the
    key is whatever the directory structure names the camera.
    """
    p = Path(image_name)
    if p.parent.name == "frames":
        return p.parent.parent.name
    return p.stem


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
    """Return ``(image_id, pose7, camera_id, name)`` per image entry."""
    out: list[tuple[int, str, int, str]] = []
    lines = path.read_text().splitlines()
    it = iter(lines)
    for ln in it:
        if not ln or ln.startswith("#"):
            continue
        parts = ln.split()
        if len(parts) < 10:
            # obs line — skip (colmap-split emits blank obs lines but any
            # stray "<x> <y> <pt3d_id>..." pattern must not confuse the parser).
            continue
        image_id = int(parts[0])
        pose7 = " ".join(parts[1:8])
        camera_id = int(parts[8])
        name = parts[9]
        out.append((image_id, pose7, camera_id, name))
        try:
            next(it)  # consume paired 2D-points line (may be empty)
        except StopIteration:
            break
    return out


def stage_images(source_dir: Path, scratch_input: Path) -> None:
    """Hardlink each undistorted image into ``scratch_input`` under flat names.

    Same reasoning as colmap-triangulate@0.2.0::stage_scratch_input — COLMAP
    resolves symlinks while enumerating ``--image_path`` and pollutes stored
    image names with the resolved path. Hardlinking avoids that.
    """
    if scratch_input.exists():
        shutil.rmtree(scratch_input)
    scratch_input.mkdir(parents=True)
    n = 0
    for src in sorted(source_dir.iterdir()):
        if not (src.is_file() or src.is_symlink()):
            continue
        real = src.resolve()
        if not real.exists():
            raise SystemExit(f"broken image link: {src} -> {real}")
        dst = scratch_input / src.name
        try:
            os.link(real, dst)
        except OSError:
            shutil.copy2(real, dst)
        n += 1
    if n == 0:
        raise SystemExit(f"no images staged from {source_dir}")


def run(cmd: list, step: str) -> None:
    print(f"[colmap-triangulate/0.3] {step}", flush=True)
    subprocess.run([str(x) for x in cmd], check=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--cameras",
        type=Path,
        required=True,
        help="colmap-cameras-txt handle (PINHOLE cameras.txt).",
    )
    ap.add_argument(
        "--poses",
        type=Path,
        required=True,
        help="colmap-images-txt handle (images.txt with pose headers).",
    )
    ap.add_argument(
        "--images",
        type=Path,
        required=True,
        help="Undistorted images/ directory (from image-undistort).",
    )
    ap.add_argument(
        "--output", type=Path, required=True, help="colmap-frame output — sparse/0/ + images/."
    )
    ap.add_argument("--scratch", type=Path, required=True)
    ap.add_argument("--use-gpu", type=int, default=1)
    ap.add_argument("--max-image-size", type=int, default=3200)
    ap.add_argument("--max-num-features", type=int, default=8192)
    ap.add_argument("--filter-max-reproj-error", type=float, default=4.0)
    args = ap.parse_args()

    cams_txt = args.cameras / "cameras.txt"
    poses_txt = args.poses / "images.txt"
    if not cams_txt.is_file():
        print(f"ERROR: missing cameras.txt at {cams_txt}", file=sys.stderr)
        return 2
    if not poses_txt.is_file():
        print(f"ERROR: missing images.txt at {poses_txt}", file=sys.stderr)
        return 2
    if not args.images.is_dir():
        print(f"ERROR: images path is not a directory: {args.images}", file=sys.stderr)
        return 2

    args.output.mkdir(parents=True, exist_ok=True)
    args.scratch.mkdir(parents=True, exist_ok=True)

    scratch_input = args.scratch / "input"
    db = args.scratch / "database.db"
    prior = args.scratch / "prior"
    sparse = args.scratch / "sparse"
    for d in (prior, sparse):
        if d.exists():
            shutil.rmtree(d)
    prior.mkdir(parents=True)
    sparse.mkdir(parents=True)
    if db.exists():
        db.unlink()

    stage_images(args.images, scratch_input)

    upstream_cams = parse_cameras_txt(cams_txt)
    upstream_poses_by_key: dict[str, str] = {}
    upstream_camera_by_key: dict[str, int] = {}
    for _iid, pose7, cid, name in parse_images_txt(poses_txt):
        if cid not in upstream_cams:
            print(
                f"ERROR: images.txt references unknown camera_id {cid} (image {name!r})",
                file=sys.stderr,
            )
            return 3
        key = cam_key(name)
        upstream_poses_by_key[key] = pose7
        upstream_camera_by_key[key] = cid
    if not upstream_poses_by_key:
        print("ERROR: no image entries parsed from images.txt", file=sys.stderr)
        return 3

    run(
        [
            "colmap",
            "feature_extractor",
            "--database_path",
            db,
            "--image_path",
            scratch_input,
            "--ImageReader.single_camera",
            "1",
            "--ImageReader.camera_model",
            "PINHOLE",
            "--SiftExtraction.max_image_size",
            args.max_image_size,
            "--SiftExtraction.max_num_features",
            args.max_num_features,
            "--FeatureExtraction.use_gpu",
            args.use_gpu,
        ],
        "feature_extractor (single_camera=1, PINHOLE)",
    )

    con = sqlite3.connect(db)
    db_rows = list(con.execute("SELECT image_id, name, camera_id FROM images ORDER BY image_id"))
    con.close()
    if not db_rows:
        print("ERROR: fresh DB has no images after feature_extractor", file=sys.stderr)
        return 3

    # Build prior aligned with fresh DB IDs. Under single_camera=1 all rows
    # share one DB camera_id — reassign the upstream PINHOLE model to it.
    (only_upstream_cam_id,) = (
        set(upstream_cams.keys())
        if len(upstream_cams) == 1
        else (
            # If upstream had >1 cameras (unusual for this pipeline), just take the
            # one that any DB image name resolves to via the poses map.
            {upstream_camera_by_key[cam_key(db_rows[0][1])]},
        )
    )
    upstream_cam_tail = upstream_cams[only_upstream_cam_id]

    cam_lines: dict[int, str] = {}
    img_lines: list[str] = []
    for image_id, name, camera_id in db_rows:
        key = cam_key(name)
        pose7 = upstream_poses_by_key.get(key)
        if pose7 is None:
            print(
                f"ERROR: no upstream pose for cam key {key!r} (DB image name {name!r}). "
                f"Available: {sorted(upstream_poses_by_key)}",
                file=sys.stderr,
            )
            return 4
        cam_lines[camera_id] = f"{camera_id} {upstream_cam_tail}"
        img_lines.append(f"{image_id} {pose7} {camera_id} {name}\n\n")

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
        ],
        "exhaustive_matcher",
    )

    # Intrinsics stay fixed here — inputs are already PINHOLE and correct.
    # If BA wants to nudge them, ban it (any drift would break the shared
    # camera model across shards).
    run(
        [
            "colmap",
            "point_triangulator",
            "--database_path",
            db,
            "--image_path",
            scratch_input,
            "--input_path",
            prior,
            "--output_path",
            sparse,
            "--clear_points",
            "1",
            "--Mapper.ba_global_function_tolerance=0.000001",
            "--Mapper.ba_refine_focal_length",
            "0",
            "--Mapper.ba_refine_principal_point",
            "0",
            "--Mapper.ba_refine_extra_params",
            "0",
            "--Mapper.filter_max_reproj_error",
            args.filter_max_reproj_error,
        ],
        "point_triangulator (intrinsics frozen; inputs already PINHOLE)",
    )

    # Assemble the colmap-frame deliverable.
    sparse_0 = args.output / "sparse" / "0"
    sparse_0.mkdir(parents=True, exist_ok=True)
    for name in ("cameras.bin", "images.bin", "points3D.bin"):
        src = sparse / name
        if src.exists():
            shutil.move(str(src), str(sparse_0 / name))
    run(
        [
            "colmap",
            "model_converter",
            "--input_path",
            sparse_0,
            "--output_path",
            sparse_0,
            "--output_type",
            "TXT",
        ],
        "model_converter → TXT",
    )
    for extra in ("rigs.txt", "frames.txt"):
        p = sparse_0 / extra
        if p.exists():
            p.unlink()

    # images/ symlinks the input undistorted frames — no re-copy needed.
    out_images = args.output / "images"
    if out_images.is_symlink() or out_images.exists():
        if out_images.is_dir() and not out_images.is_symlink():
            shutil.rmtree(out_images)
        else:
            out_images.unlink()
    out_images.symlink_to(args.images.resolve())

    for required in (
        "sparse/0/cameras.bin",
        "sparse/0/images.bin",
        "sparse/0/points3D.bin",
        "images",
    ):
        if not (args.output / required).exists():
            print(f"ERROR: missing required output: {args.output / required}", file=sys.stderr)
            return 4
    print(f"[colmap-triangulate/0.3] done → {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
