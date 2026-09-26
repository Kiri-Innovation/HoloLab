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
import os
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
    CAMERA_MODEL_OPENCV,
    add_camera,
    add_image,
    create_empty_db,
    export_manual_rigs_frames_txt,
    repair_frames_if_needed,
)
from sfm_key import (  # noqa: E402
    build_numeric_sfm_index,
    cam_key,
    resolve_sfm_key,
)


def parse_cameras_txt(path: Path) -> dict[int, tuple[str, int, int, list[float]]]:
    """camera_id -> (model, width, height, params). Only OPENCV expected here."""
    out: dict[int, tuple[str, int, int, list[float]]] = {}
    for ln in path.read_text().splitlines():
        if not ln or ln.startswith("#"):
            continue
        parts = ln.split()
        cid = int(parts[0])
        model = parts[1]
        width = int(parts[2])
        height = int(parts[3])
        params = [float(x) for x in parts[4:]]
        out[cid] = (model, width, height, params)
    return out


def parse_images_txt(path: Path) -> list[tuple[int, np.ndarray, np.ndarray, int, str]]:
    """Return ``(image_id, qvec[4], tvec[3], camera_id, name)`` per image entry."""
    out: list[tuple[int, np.ndarray, np.ndarray, int, str]] = []
    lines = path.read_text().splitlines()
    it = iter(lines)
    for ln in it:
        if not ln or ln.startswith("#"):
            continue
        parts = ln.split()
        if len(parts) < 10:
            continue
        iid = int(parts[0])
        qvec = np.array([float(x) for x in parts[1:5]], dtype=np.float64)
        tvec = np.array([float(x) for x in parts[5:8]], dtype=np.float64)
        cid = int(parts[8])
        name = parts[9]
        out.append((iid, qvec, tvec, cid, name))
        try:
            next(it)
        except StopIteration:
            break
    return out


def stage_scratch_input(source_dir: Path, scratch_input: Path) -> list[str]:
    """Hardlink each per-cam file into ``scratch_input`` under a flat name."""
    if scratch_input.exists():
        shutil.rmtree(scratch_input)
    scratch_input.mkdir(parents=True)
    names: list[str] = []
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
        names.append(src.name)
    if not names:
        raise SystemExit(f"no images staged from {source_dir}")
    return names


def prefill_db(
    db_path: Path,
    manual_dir: Path,
    image_names: list[str],
    sfm_cams: dict[int, tuple[str, int, int, list[float]]],
    sfm_pose_by_key: dict[str, tuple[np.ndarray, np.ndarray]],
) -> None:
    """Replicate STG's ``convert_selected_cam_matrix_to_colmapdb``."""
    create_empty_db(db_path)
    manual_dir.mkdir(parents=True, exist_ok=True)

    if len(sfm_cams) != 1:
        raise SystemExit(
            f"expected exactly 1 camera in SfM cameras.txt (single_camera=1); got {len(sfm_cams)}"
        )
    (only_sfm_cid,) = sfm_cams.keys()
    model_name, width, height, params = sfm_cams[only_sfm_cid]
    if model_name != "OPENCV":
        raise SystemExit(f"SfM cameras.txt model must be OPENCV; got {model_name!r}")
    params_arr = np.asarray(params, dtype=np.float64)

    cameras_txt_lines: list[str] = []
    images_txt_lines: list[str] = []

    numeric_index = build_numeric_sfm_index(sfm_pose_by_key)
    resolved: list[tuple[str, str]] = []
    con = sqlite3.connect(str(db_path))
    try:
        for i, name in enumerate(image_names):
            resolved_key = resolve_sfm_key(name, sfm_pose_by_key, numeric_index)
            if resolved_key is None:
                raise SystemExit(
                    f"no SfM pose for staged image {name!r} "
                    f"(direct stem {cam_key(name)!r} not found; numeric fallback "
                    f"{'unavailable — ambiguous SfM keys' if numeric_index is None else 'no match'}). "
                    f"Available SfM keys: {sorted(sfm_pose_by_key)}"
                )
            qvec, tvec = sfm_pose_by_key[resolved_key]
            resolved.append((name, resolved_key))
            im_id = i + 1
            cam_id = add_camera(con, CAMERA_MODEL_OPENCV, width, height, params_arr)
            add_image(con, name=name, camera_id=cam_id, prior_q=qvec, prior_t=tvec, image_id=im_id)

            id_str = str(im_id)
            q_str = " ".join(map(str, qvec))
            t_str = " ".join(map(str, tvec))
            images_txt_lines.append(f"{id_str} {q_str} {t_str} {id_str} {name}\n")
            images_txt_lines.append("\n")

            p_str = " ".join(params_arr.astype(str))
            cameras_txt_lines.append(f"{id_str} OPENCV {width} {height} {p_str} \n")
        con.commit()
    finally:
        con.close()

    (manual_dir / "images.txt").write_text("".join(images_txt_lines))
    (manual_dir / "cameras.txt").write_text("".join(cameras_txt_lines))
    (manual_dir / "points3D.txt").write_text("")

    fallback_hits = sum(1 for name, key in resolved if cam_key(name) != key)
    if fallback_hits:
        preview = ", ".join(f"{name}→{key}" for name, key in resolved[:3])
        more = f" (+{len(resolved) - 3} more)" if len(resolved) > 3 else ""
        print(
            f"[colmap-triangulate/0.7] staged→SfM key mapping "
            f"({fallback_hits}/{len(resolved)} via numeric fallback): {preview}{more}",
            flush=True,
        )


def run(cmd: list, step: str) -> None:
    print(f"[colmap-triangulate/0.7] {step}", flush=True)
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
    if not args.image_path.is_dir():
        print(f"ERROR: image path is not a directory: {args.image_path}", file=sys.stderr)
        return 2

    has_direct_files = any(
        p.is_file() and not p.name.startswith(".") for p in args.image_path.iterdir()
    )
    image_source = args.image_path if has_direct_files else args.image_path / "frames"
    if not image_source.is_dir():
        print(
            f"ERROR: no image files at {args.image_path} or its frames/ subdir",
            file=sys.stderr,
        )
        return 2

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
    sfm_pose_by_key: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for _iid, qvec, tvec, _cid, name in parse_images_txt(imgs_txt):
        sfm_pose_by_key[cam_key(name)] = (qvec, tvec)
    if not sfm_pose_by_key:
        print("ERROR: no image entries parsed from SfM images.txt", file=sys.stderr)
        return 3

    prefill_db(db, manual, image_names, sfm_cams, sfm_pose_by_key)

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
        ],
        "feature_extractor (DB pre-populated with per-image OPENCV cameras)",
    )

    repair_frames_if_needed(db)
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
    shutil.copyfile(src_points, args.output / "points3D.txt")

    if not (args.output / "points3D.txt").is_file():
        print(f"ERROR: missing output points3D.txt at {args.output}", file=sys.stderr)
        return 4

    print(f"[colmap-triangulate/0.7] done → {args.output / 'points3D.txt'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
