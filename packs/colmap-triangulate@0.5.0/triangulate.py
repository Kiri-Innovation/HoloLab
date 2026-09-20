#!/usr/bin/env python3
"""Per-frame triangulate + inline undistort — strict mirror of STG's `getcolmapsinglen3d`.

Each shard replays the exact per-frame recipe from
``SpacetimeGaussians/thirdparty/gaussian_splatting/helper3dg.py:440-503``:

    pre-fill DB (per-image OPENCV cameras + prior_q/prior_t)
      -> feature_extractor (no --single_camera / --camera_model — DB drives it)
      -> repair frames/rigs (COLMAP 3.11+)
      -> exhaustive_matcher
      -> point_triangulator (only --Mapper.ba_global_function_tolerance=0.000001;
         BA is free to refine each per-image OPENCV intrinsic, mirroring STG)
      -> image_undistorter (OPENCV -> PINHOLE + undistorted images/)
      -> move sparse/*.bin into sparse/0/ and write TXT mirror

The DB pre-fill is the load-bearing shape difference vs @0.4.0: each image
gets its **own** OPENCV camera row with the SfM's intrinsic + this frame's
per-cam pose written as ``prior_q`` / ``prior_t``. ``feature_extractor``
then finds each image by ``name`` and attaches keypoints to the pre-existing
camera_id without inserting anything new — matching the original
``convert_selected_cam_matrix_to_colmapdb`` → ``getcolmapsinglen3d`` chain.
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

# Vendored COLMAP DB helpers live next to this file so the pack is self-contained.
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


def cam_key(image_name: str) -> str:
    """Per-camera key from a COLMAP-stored image name.

    * flat: ``cam01.png`` -> ``cam01`` (stem).
    * resolved-symlink: ``.../<cam>/frames/<basename>`` -> ``<cam>`` (grandparent).
    """
    p = Path(image_name)
    if p.parent.name == "frames":
        return p.parent.parent.name
    return p.stem


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
            continue  # obs line — skip
        iid = int(parts[0])
        qvec = np.array([float(x) for x in parts[1:5]], dtype=np.float64)
        tvec = np.array([float(x) for x in parts[5:8]], dtype=np.float64)
        cid = int(parts[8])
        name = parts[9]
        out.append((iid, qvec, tvec, cid, name))
        try:
            next(it)  # consume paired 2D-points line
        except StopIteration:
            break
    return out


def stage_scratch_input(source_dir: Path, scratch_input: Path) -> list[str]:
    """Hardlink each per-cam file into ``scratch_input`` under a flat name.

    Same rationale as @0.2.0::stage_scratch_input: COLMAP resolves symlinks
    when walking ``--image_path``, which pushes ``image_undistorter``
    outputs outside the deliverable. Hardlink into a clean scratch dir.
    Returns the list of staged base-names in sorted order.
    """
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
    """Replicate STG's ``convert_selected_cam_matrix_to_colmapdb`` (pre_no_prior.py:77-142).

    * Fresh DB.
    * Per image (in sorted order matching ``image_names``): insert an OWN
      OPENCV camera row filled with the SfM's shared intrinsic, then insert
      the image with ``image_id = i+1`` and prior_q / prior_t from the SfM
      pose for that camera key.
    * Also write ``manual/{cameras,images,points3D}.txt`` because
      ``point_triangulator`` reads them; ``export_manual_rigs_frames_txt``
      may then top them up with rigs/frames after feature_extractor.
    """
    create_empty_db(db_path)
    manual_dir.mkdir(parents=True, exist_ok=True)

    if len(sfm_cams) != 1:
        # SfM was run with single_camera=1 so there's one entry; carry any
        # extras through for completeness but assert to fail loud if two
        # different OPENCV rigs snuck in — the pre-fill logic below assumes
        # one intrinsic shared across images.
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

    con = sqlite3.connect(str(db_path))
    try:
        for i, name in enumerate(image_names):
            key = cam_key(name)
            pose = sfm_pose_by_key.get(key)
            if pose is None:
                raise SystemExit(
                    f"no SfM pose for cam key {key!r} (staged image {name!r}). "
                    f"Available keys: {sorted(sfm_pose_by_key)}"
                )
            qvec, tvec = pose
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


def run(cmd: list, step: str) -> None:
    print(f"[colmap-triangulate/0.5] {step}", flush=True)
    subprocess.run([str(x) for x in cmd], check=True)


def move_sparse_to_zero(sparse_root: Path) -> Path:
    """Move ``image_undistorter``'s flat sparse/*.bin outputs into sparse/0/.

    Mirrors ``helper3dg.py:496-503``. Returns the ``sparse/0`` path.
    """
    sparse_0 = sparse_root / "0"
    sparse_0.mkdir(parents=True, exist_ok=True)
    for name in ("cameras.bin", "images.bin", "points3D.bin"):
        src = sparse_root / name
        if src.exists():
            shutil.move(str(src), str(sparse_0 / name))
    return sparse_0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--cams",
        type=Path,
        required=True,
        help="colmap-cams handle from SfM (cameras.txt OPENCV + images.txt with poses).",
    )
    ap.add_argument(
        "--image-path",
        type=Path,
        required=True,
        help="This frame's per-camera image directory (from regroup-by-frame).",
    )
    ap.add_argument(
        "--output",
        type=Path,
        required=True,
        help="colmap output — self-contained frame dir (sparse/0/ + images/).",
    )
    ap.add_argument("--scratch", type=Path, required=True)
    ap.add_argument("--use-gpu", type=int, default=1)
    args = ap.parse_args()

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

    args.output.mkdir(parents=True, exist_ok=True)
    args.scratch.mkdir(parents=True, exist_ok=True)

    scratch_input = args.scratch / "input"
    db = args.scratch / "input.db"
    manual = args.scratch / "manual"
    distorted_sparse = args.scratch / "distorted_sparse"
    for d in (distorted_sparse,):
        if d.exists():
            shutil.rmtree(d)
    distorted_sparse.mkdir(parents=True)
    if manual.exists():
        shutil.rmtree(manual)

    # 1. Stage images into flat scratch/input/.
    image_names = stage_scratch_input(args.image_path, scratch_input)

    # 2. Parse SfM outputs, index poses by cam key.
    sfm_cams = parse_cameras_txt(cams_txt)
    sfm_pose_by_key: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for _iid, qvec, tvec, _cid, name in parse_images_txt(imgs_txt):
        sfm_pose_by_key[cam_key(name)] = (qvec, tvec)
    if not sfm_pose_by_key:
        print("ERROR: no image entries parsed from SfM images.txt", file=sys.stderr)
        return 3

    # 3. Pre-fill DB (per-image OPENCV cameras + prior_q/prior_t) + write manual/*.txt.
    prefill_db(db, manual, image_names, sfm_cams, sfm_pose_by_key)

    # 4. feature_extractor — no --single_camera / --camera_model, matching
    #    helper3dg.py:456 (DB is pre-populated; extractor just attaches features).
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
        ],
        "feature_extractor (DB pre-populated with per-image OPENCV cameras)",
    )

    # 5. Repair COLMAP 3.11+ frames/rigs from current images+rigs, then
    #    export manual/rigs.txt + manual/frames.txt aligned with the DB.
    repair_frames_if_needed(db)
    export_manual_rigs_frames_txt(db, manual)

    # 6. exhaustive_matcher — mirror helper3dg.py:464.
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

    # 7. point_triangulator — ONLY --Mapper.ba_global_function_tolerance=0.000001
    #    (helper3dg.py:470-471). BA is free to refine each per-image OPENCV
    #    intrinsic (no --ba_refine_*=0). No --clear_points, no --filter_max_reproj_error.
    run(
        [
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
        ],
        "point_triangulator (BA global tol 1e-6, per-image intrinsics free)",
    )

    # 8. image_undistorter — mirror helper3dg.py:479-480. No --blank_pixels
    #    (default 0 = crop invalid regions), --output_type COLMAP.
    for stale in (
        "images",
        "sparse",
        "stereo",
        "run-colmap-geometric.sh",
        "run-colmap-photometric.sh",
    ):
        p = args.output / stale
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
    run(
        [
            "colmap",
            "image_undistorter",
            "--image_path",
            scratch_input,
            "--input_path",
            distorted_sparse,
            "--output_path",
            args.output,
            "--output_type",
            "COLMAP",
        ],
        "image_undistorter (OPENCV -> PINHOLE)",
    )

    # 9. Reshape output: move sparse/*.bin into sparse/0/, drop stereo/ helpers.
    sparse_root = args.output / "sparse"
    sparse_0 = move_sparse_to_zero(sparse_root)
    for stale in ("stereo", "run-colmap-geometric.sh", "run-colmap-photometric.sh"):
        p = args.output / stale
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
    for stale in ("rigs.bin", "frames.bin"):
        p = sparse_root / stale
        if p.exists():
            p.unlink()

    # 10. TXT mirror for grep-ability; STG reader prefers .bin but keeping both is cheap.
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
        "model_converter -> TXT",
    )
    for extra in ("rigs.txt", "frames.txt"):
        p = sparse_0 / extra
        if p.exists():
            p.unlink()

    # 11. Sanity gate.
    for required in (
        "sparse/0/cameras.bin",
        "sparse/0/images.bin",
        "sparse/0/points3D.bin",
        "images",
    ):
        if not (args.output / required).exists():
            print(f"ERROR: missing required output: {args.output / required}", file=sys.stderr)
            return 4
    if not any((args.output / "images").iterdir()):
        print(f"ERROR: undistorted images/ is empty: {args.output / 'images'}", file=sys.stderr)
        return 4

    print(f"[colmap-triangulate/0.5] done -> {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
