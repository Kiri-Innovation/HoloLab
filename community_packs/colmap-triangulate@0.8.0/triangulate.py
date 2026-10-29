#!/usr/bin/env python3
"""Per-frame triangulate — mirror of STG's ``getcolmapsinglen3d`` minus the undistort tail.

Each shard replays the pre-undistort half of
``SpacetimeGaussians/thirdparty/gaussian_splatting/helper3dg.py:440-503``:

    pre-fill DB (per-image OPENCV cameras + prior_q/prior_t)
      -> feature_extractor (no --single_camera / --camera_model — DB drives it)
      -> repair frames/rigs (COLMAP 3.11+)
      -> exhaustive_matcher
      -> point_triangulator (only --Mapper.ba_global_function_tolerance=0.000001;
         given-pose triangulator — poses stay fixed, intrinsics stay fixed
         unless --refine-intrinsics is passed)
      -> emit points3D.txt on the output handle

Compared to @0.6.0:
    * No image_undistorter call.
    * Output is a bare ``point-cloud`` handle (points3D.txt only), not a
      self-contained per-frame folder.
    * All intrinsics/pose bookkeeping stays inside scratch — the caller
      keeps whatever cams+intrs it wanted, this node only contributes points.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import numpy as np

_PACK_DIR = Path(__file__).resolve().parent
if str(_PACK_DIR) not in sys.path:
    sys.path.insert(0, str(_PACK_DIR))

from colmap_db import (  # noqa: E402
    CAMERA_MODELS,
    add_camera,
    add_image,
    create_empty_db,
    export_manual_rigs_frames_txt,
    repair_frames_if_needed,
)
from position_model import (  # noqa: E402
    camera_rows,
    copy_auxiliary,
    paired_poses,
    read_images,
    stage_images,
    validate_tracks,
    write_images,
)


def parse_cameras_txt(path: Path) -> dict[int, tuple[str, int, int, list[float]]]:
    return {
        cid: (row[1], int(row[2]), int(row[3]), list(map(float, row[4:])))
        for cid, row in camera_rows(path).items()
    }


def parse_images_txt(path: Path) -> list[tuple[int, np.ndarray, np.ndarray, int, str]]:
    return [
        (
            int(row[0]),
            np.asarray(row[1:5], dtype=np.float64),
            np.asarray(row[5:8], dtype=np.float64),
            int(row[8]),
            row[9],
        )
        for row, _obs in read_images(path)
    ]


def stage_scratch_input(source_dir: Path, scratch_input: Path) -> list[str]:
    return [path.name for path in stage_images(source_dir, scratch_input)]


def prefill_db(
    db_path: Path, manual_dir: Path, image_names: list[str], sfm_cams: dict, sfm_poses: list
) -> list[tuple[str, int]]:
    """Zip positions strictly; keep each input model ID as an internal reference."""
    if len(image_names) != len(sfm_poses) or not image_names:
        raise ValueError(
            f"position length mismatch: images={len(image_names)}, poses={len(sfm_poses)}"
        )
    if len({pose[0] for pose in sfm_poses}) != len(sfm_poses):
        raise ValueError("duplicate IMAGE_ID")
    for iid, qvec, tvec, cid, _name in sfm_poses:
        if (
            iid <= 0
            or cid not in sfm_cams
            or not np.isfinite(qvec).all()
            or not np.isfinite(tvec).all()
        ):
            raise ValueError("invalid pose or camera reference")
        model, width, height, params = sfm_cams[cid]
        if model not in CAMERA_MODELS or min(width, height) <= 0 or not np.isfinite(params).all():
            raise ValueError("unsupported or invalid camera model")
    create_empty_db(db_path)
    manual_dir.mkdir(parents=True, exist_ok=True)
    camera_lines, image_lines, resolved = [], [], []
    inserted = set()
    with sqlite3.connect(str(db_path)) as con:
        for name, (iid, qvec, tvec, cid, _old_name) in zip(image_names, sfm_poses, strict=True):
            model, width, height, params = sfm_cams[cid]
            if cid not in inserted:
                add_camera(
                    con, CAMERA_MODELS[model], width, height, np.asarray(params), camera_id=cid
                )
                inserted.add(cid)
                camera_lines.append(
                    f"{cid} {model} {width} {height} " + " ".join(map(str, params)) + "\n"
                )
            add_image(con, name=name, camera_id=cid, prior_q=qvec, prior_t=tvec, image_id=iid)
            row = [str(iid), *map(str, qvec), *map(str, tvec), str(cid), name]
            image_lines.append((row, ""))
            resolved.append((name, iid))
    (manual_dir / "cameras.txt").write_text("".join(camera_lines))
    write_images(manual_dir / "images.txt", image_lines)
    (manual_dir / "points3D.txt").write_text("")
    return resolved


def complete_output_model(prior: Path, output: Path) -> None:
    """Retain zero-observation poses without inventing any missing input pose."""
    seeds = {int(row[0]): (row, "") for row, _ in read_images(prior / "images.txt")}
    data = (output / "images.txt").read_text().splitlines()
    actual = (
        read_images(output / "images.txt")
        if any(line.strip() and not line.lstrip().startswith("#") for line in data)
        else []
    )
    actual_by_id = {int(row[0]): (row, obs) for row, obs in actual}
    if not set(actual_by_id) <= set(seeds):
        raise ValueError("triangulator introduced unknown IMAGE_ID")
    cameras = parse_cameras_txt(output / "cameras.txt")
    prior_cameras = parse_cameras_txt(prior / "cameras.txt")
    for iid, (row, _obs) in actual_by_id.items():
        if row[9] != seeds[iid][0][9] or row[8] != seeds[iid][0][8] or int(row[8]) not in cameras:
            raise ValueError("triangulator changed a staged slot/reference")
    merged = [actual_by_id.get(iid, seeds[iid]) for iid in sorted(seeds)]
    for row, _ in merged:
        cid = int(row[8])
        if cid not in cameras:
            cameras[cid] = prior_cameras[cid]
    write_images(output / "images.txt", merged)
    (output / "cameras.txt").write_text(
        "".join(
            f"{cid} {model} {width} {height} " + " ".join(map(str, params)) + "\n"
            for cid, (model, width, height, params) in sorted(cameras.items())
        )
    )
    copy_auxiliary(prior, output, {iid: iid for iid in seeds})
    validate_tracks(output / "images.txt", output / "points3D.txt")


def validate_manual_references(
    db_path: Path, manual_dir: Path, missing_pose_names: list[str]
) -> None:
    """Check the exact DB rows the rig/frame exporter will serialize."""
    camera_ids = set(parse_cameras_txt(manual_dir / "cameras.txt"))
    images = {
        iid: (cid, name) for iid, _q, _t, cid, name in parse_images_txt(manual_dir / "images.txt")
    }
    errors: list[str] = []
    with sqlite3.connect(str(db_path)) as con:
        db_images = {
            iid: (cid, name)
            for iid, cid, name in con.execute("SELECT image_id, camera_id, name FROM images")
        }
        if db_images != images:
            errors.append(f"DB/manual image mappings differ: DB={db_images}, manual={images}")
        db_cameras = {row[0] for row in con.execute("SELECT camera_id FROM cameras")}
        if db_cameras != camera_ids or {cid for cid, _ in images.values()} != camera_ids:
            errors.append("DB/manual camera IDs and posed image camera IDs differ")
        for iid, (cid, name) in images.items():
            if cid not in camera_ids:
                errors.append(f"image {name!r} (id={iid}) references missing camera {cid}")
        tables = {
            row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "rigs" in tables:
            rigs = dict(
                con.execute("SELECT rig_id, ref_sensor_id FROM rigs WHERE ref_sensor_type=0")
            )
            for rid, cid in rigs.items():
                if cid not in camera_ids:
                    errors.append(f"rig {rid} references missing camera {cid}")
            frames = dict(con.execute("SELECT frame_id, rig_id FROM frames"))
            frame_images = set()
            for fid, iid, cid, stype in con.execute(
                "SELECT frame_id, data_id, sensor_id, sensor_type FROM frame_data"
            ):
                frame_images.add(iid)
                if stype != 0 or cid not in camera_ids or images.get(iid, (None,))[0] != cid:
                    errors.append(f"frame {fid} references invalid camera {cid}/image {iid}")
                if fid != iid or rigs.get(frames.get(fid)) != cid:
                    errors.append(f"frame {fid} has no matching posed image/rig for camera {cid}")
            if frame_images != set(images) or set(frames) != set(images):
                errors.append("frame IDs/data IDs do not match the posed image IDs")
    if errors:
        raise SystemExit(
            "Invalid manual COLMAP model before rig/frame export: "
            + "; ".join(errors)
            + f"; {len(missing_pose_names)} staged image(s) missing SfM poses: "
            + (", ".join(missing_pose_names) or "(none)")
        )


def run(cmd: list, step: str) -> None:
    print(f"[colmap-triangulate/0.8.0] {step}", flush=True)
    subprocess.run([str(x) for x in cmd], check=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cams", type=Path, required=True)
    ap.add_argument("--refine-intrinsics", type=int, choices=(0, 1), default=0)
    ap.add_argument("--image-path", type=Path, required=True)
    ap.add_argument(
        "--output",
        type=Path,
        required=True,
        help="point-cloud handle root — the script writes points3D.txt directly here.",
    )
    ap.add_argument("--scratch", type=Path, required=True)
    ap.add_argument("--use-gpu", type=int, default=1)
    ap.add_argument("--sift-max-num-features", type=int, default=8192)
    ap.add_argument("--sift-max-image-size", type=int, default=3200)
    ap.add_argument("--sift-domain-size-pooling", type=int, choices=(0, 1), default=0)
    ap.add_argument("--sift-estimate-affine-shape", type=int, choices=(0, 1), default=0)
    ap.add_argument(
        "--ba-max-refinements",
        type=int,
        default=2,
        help="Ceres --Mapper.ba_global_max_refinements. Upstream default 5; "
        "2 is the perf/quality sweet spot on the classic STG recipe.",
    )
    args = ap.parse_args()
    refine_intrinsics = bool(args.refine_intrinsics)
    if args.ba_max_refinements < 1:
        print(
            f"ERROR: --ba-max-refinements must be >= 1; got {args.ba_max_refinements}",
            file=sys.stderr,
        )
        return 2

    cams_txt = args.cams / "cameras.txt"
    imgs_txt = args.cams / "images.txt"
    if not cams_txt.is_file() or not imgs_txt.is_file():
        print(
            f"ERROR: cams input missing cameras.txt / images.txt under {args.cams}",
            file=sys.stderr,
        )
        return 2
    image_source = args.image_path

    args.output.mkdir(parents=True, exist_ok=True)
    args.scratch.mkdir(parents=True, exist_ok=True)

    scratch_input = args.scratch / "input"
    db = args.scratch / "input.db"
    manual = args.scratch / "manual"
    distorted_sparse = args.scratch / "distorted_sparse"
    if distorted_sparse.exists():
        shutil.rmtree(distorted_sparse)
    distorted_sparse.mkdir(parents=True)
    if manual.exists():
        shutil.rmtree(manual)

    image_names = stage_scratch_input(image_source, scratch_input)

    sfm_cams = parse_cameras_txt(cams_txt)
    paired_poses(args.cams, [scratch_input / name for name in image_names])
    sfm_poses = parse_images_txt(imgs_txt)
    resolved = prefill_db(db, manual, image_names, sfm_cams, sfm_poses)
    used_names = [name for name, _iid in resolved]
    missing_pose_names = []
    if len(used_names) < 2:
        raise ValueError("need at least two positions to triangulate")

    # Namespaced ``num_threads`` caps for each COLMAP subcommand. The OMP
    # env exports in the manifest preamble only pinch the inner
    # Eigen/OpenBLAS layer; these flags size the subcommand's own worker
    # pool. Belt on top of the manifest braces. Each subcommand exposes
    # its own namespaced option — there is no top-level ``--num_threads``
    # on these executables in the COLMAP build shipped in the ``kiri``
    # env. Raised 2→4 in @0.7.2: at par=5 the descriptor extract and
    # BA both scale cleanly to 4 threads/shard.
    run(
        [
            "colmap",
            "feature_extractor",
            "--database_path",
            db,
            "--image_path",
            scratch_input,
            "--FeatureExtraction.use_gpu",
            args.use_gpu,
            "--FeatureExtraction.num_threads",
            "4",
            "--SiftExtraction.max_num_features",
            str(args.sift_max_num_features),
            "--SiftExtraction.max_image_size",
            str(args.sift_max_image_size),
            "--SiftExtraction.domain_size_pooling",
            str(args.sift_domain_size_pooling),
            "--SiftExtraction.estimate_affine_shape",
            str(args.sift_estimate_affine_shape),
        ],
        "feature_extractor (DB pre-populated with per-image cameras)",
    )

    repair_frames_if_needed(db)
    validate_manual_references(db, manual, missing_pose_names)
    export_manual_rigs_frames_txt(db, manual)

    run(
        [
            "colmap",
            "exhaustive_matcher",
            "--database_path",
            db,
            "--FeatureMatching.use_gpu",
            args.use_gpu,
            "--FeatureMatching.num_threads",
            "4",
        ],
        "exhaustive_matcher",
    )

    pt_cmd = [
        "colmap",
        "point_triangulator",
        "--database_path",
        db,
        "--image_path",
        scratch_input,
        "--input_path",
        manual,
        "--output_path",
        distorted_sparse,
        "--Mapper.ba_global_function_tolerance=0.000001",
        f"--Mapper.ba_global_max_refinements={args.ba_max_refinements}",
        "--Mapper.num_threads",
        "4",
    ]
    if refine_intrinsics:
        pt_cmd.append("--refine_intrinsics")
        pt_cmd.append("1")
    run(
        pt_cmd,
        "point_triangulator (BA global tol 1e-6, poses fixed"
        + (", intrinsics refined)" if refine_intrinsics else ", intrinsics fixed)"),
    )

    # Convert BIN → TXT and extract just points3D.txt onto the output handle.
    run(
        [
            "colmap",
            "model_converter",
            "--input_path",
            distorted_sparse,
            "--output_path",
            distorted_sparse,
            "--output_type",
            "TXT",
        ],
        "model_converter → TXT",
    )
    src_points = distorted_sparse / "points3D.txt"
    if not src_points.is_file():
        print(f"ERROR: point_triangulator produced no points3D.txt: {src_points}", file=sys.stderr)
        return 4
    complete_output_model(manual, distorted_sparse)
    shutil.copyfile(src_points, args.output / "points3D.txt")
    # Track indices belong to this reconstruction's features, not the SfM seed.
    shutil.copyfile(distorted_sparse / "images.txt", args.output / "images.txt")
    shutil.copyfile(distorted_sparse / "cameras.txt", args.output / "cameras.txt")
    (args.output / "triangulation_meta.json").write_text(
        json.dumps(
            {
                "used_image_files": sorted(used_names),
                "used_sfm_image_ids": [iid for _name, iid in resolved],
                "used_camera_count": len(used_names),
                "missing_pose_image_files": missing_pose_names,
            },
            indent=2,
        )
        + "\n"
    )

    if not (args.output / "points3D.txt").is_file():
        print(f"ERROR: missing output points3D.txt at {args.output}", file=sys.stderr)
        return 4

    print(f"[colmap-triangulate/0.8.0] done → {args.output / 'points3D.txt'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
