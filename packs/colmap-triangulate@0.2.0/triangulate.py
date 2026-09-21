#!/usr/bin/env python3
"""Triangulate 3D points against known SfM poses, then undistort to PINHOLE.

Mirrors ``SpacetimeGaussians/thirdparty/gaussian_splatting/helper3dg.py::getcolmapsinglen3d``
per shard:

    feature_extractor (single_camera=1, OPENCV)
      -> rewrite prior IDs to match fresh DB
      -> exhaustive_matcher
      -> point_triangulator (BA global tol 1e-6; given-pose, intrinsics
         fixed too — --refine_intrinsics defaults to 0, never set here.
         Erratum: earlier docstring claimed intrinsics were refined; see
         manifest doc-erratum block for the correction.)
      -> image_undistorter (OPENCV -> PINHOLE + undistorted images/)

The two subtleties worth spelling out:

1. **COLMAP resolves symlinks** while walking ``--image_path`` — verified on
   v0.1.0 outputs (image names came out as
   ``../../../<extractor-uuid>/frame_sequence/<cam>/frames/<basename>``).
   ``image_undistorter`` would then write ``output/images/<that same path>``,
   escaping the output dir. Fix: hardlink each per-cam file into a clean
   scratch ``input/`` with a flat ``<cam>.<ext>`` name (rig_common.py:157-177
   uses the same trick). Feature extraction and undistortion then run against
   flat names and outputs stay inside the frame dir.
2. **COLMAP 3.11+ rig-consistency check**: ``Reconstruction::Load`` asserts
   that for each DB image_id X, the prior's image_id X references the same
   camera_id. SQLite auto-increments in insertion order; the extractor's
   reader threads race, so mechanically copying the SfM's cameras.txt /
   images.txt into the prior fails. We run feature_extractor first, query
   the assigned (image_id, name, camera_id), then synthesise a prior aligned
   with the DB by construction. Intrinsics + poses come from the SfM output
   via a per-camera key (``cam_key`` handles both flat and resolved-path
   name shapes for forward compat).
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

    Handles two shapes:

    * **flat**: ``cam01.png`` (this pack's own feature_extractor input)
      → key = ``cam01`` (stem).
    * **resolved-symlink**: ``.../<cam>/frames/<basename>``
      (the shape v0.1.0's colmap-sfm-cams-only output stored after
      COLMAP canonicalised the ``regroup-by-frame`` symlinks)
      → key = ``<cam>`` (grandparent name).

    Both SfM and triangulate see the same cam identifiers on each side, so
    key equality holds even if one side has resolved paths and the other
    flat.
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
        image_id = int(parts[0])
        pose7 = " ".join(parts[1:8])
        camera_id = int(parts[8])
        name = parts[9]
        out.append((image_id, pose7, camera_id, name))
        try:
            next(it)  # consume paired 2D-points line
        except StopIteration:
            break
    return out


def stage_scratch_input(source_dir: Path, scratch_input: Path) -> None:
    """Hardlink (fallback: copy) each per-cam file into ``scratch_input`` with a flat name.

    ``source_dir`` is ``<inputs.frames>/frames`` — one symlink per rig cam
    from ``regroup-by-frame``, each pointing at the raw video-extracted frame.
    We copy/link into a fresh scratch dir with the ORIGINAL symlink name
    (``cam01.png`` etc.), then feature_extract that. Downstream
    ``image_undistorter`` writes to ``output/images/cam01.png``, cleanly
    inside the output dir.
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
    print(f"[colmap-triangulate] {step}", flush=True)
    subprocess.run([str(x) for x in cmd], check=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--cams",
        type=Path,
        required=True,
        help="Upstream colmap-cams handle (cameras.txt + images.txt, OPENCV model).",
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
        help="colmap-frame output — self-contained frame dir (sparse/0/ + images/).",
    )
    ap.add_argument(
        "--scratch",
        type=Path,
        required=True,
        help="Scratch dir for the intermediate DB, staged input, prior, "
        "and pre-undistort sparse model.",
    )
    ap.add_argument("--use-gpu", type=int, default=1)
    ap.add_argument("--max-image-size", type=int, default=3200)
    ap.add_argument("--max-num-features", type=int, default=8192)
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

    scratch_input = args.scratch / "input"
    db = args.scratch / "database.db"
    prior = args.scratch / "prior"
    distorted_sparse = args.scratch / "distorted_sparse"
    for d in (prior, distorted_sparse):
        if d.exists():
            shutil.rmtree(d)
    prior.mkdir(parents=True)
    distorted_sparse.mkdir(parents=True)
    if db.exists():
        db.unlink()

    stage_scratch_input(args.image_path, scratch_input)

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

    # single_camera=1 + OPENCV mirrors colmap-sfm-cams-only@0.2.0 so DB
    # camera_id assignments match the SfM's single shared rig camera.
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
            "OPENCV",
            "--SiftExtraction.max_image_size",
            args.max_image_size,
            "--SiftExtraction.max_num_features",
            args.max_num_features,
            "--FeatureExtraction.use_gpu",
            args.use_gpu,
        ],
        "feature_extractor (single_camera=1, OPENCV)",
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
        # All DB rows share the same camera_id under single_camera=1 → this
        # dict has exactly one entry, idempotently reassigned.
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
        ],
        "exhaustive_matcher",
    )

    # No --Mapper.ba_refine_*=0 flags — mirrors helper3dg.py:465-466 which
    # only sets the BA tolerance. NB: those Mapper flags are inert inside
    # point_triangulator anyway (they're Mapper options). Intrinsics stay
    # fixed here because COLMAP's --refine_intrinsics defaults to 0 and we
    # never set it — matching STG's actual behaviour. Earlier comments in
    # this file claimed BA was refining intrinsics; see the manifest
    # doc-erratum block for the correction.
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
            distorted_sparse,
            "--clear_points",
            "1",
            "--Mapper.ba_global_function_tolerance=0.000001",
            "--Mapper.filter_max_reproj_error",
            args.filter_max_reproj_error,
        ],
        "point_triangulator (BA global tol 1e-6, poses fixed, intrinsics fixed)",
    )

    # image_undistorter reads the OPENCV sparse model + raw images and
    # emits PINHOLE cameras + undistorted images. Output layout:
    #   <output>/images/<cam>.<ext>
    #   <output>/sparse/{cameras,images,points3D}.bin  (top-level, no /0/)
    # We then move the .bin files into sparse/0/ to match the layout every
    # downstream reader (STG's dataset_readers, colmap-assemble) expects.
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

    sparse_root = args.output / "sparse"
    sparse_0 = sparse_root / "0"
    sparse_0.mkdir(parents=True, exist_ok=True)
    for name in ("cameras.bin", "images.bin", "points3D.bin"):
        src = sparse_root / name
        if src.exists():
            shutil.move(str(src), str(sparse_0 / name))
    # image_undistorter emits stereo/ + shell scripts + COLMAP-3.11 rig/frame
    # byproducts (rigs.bin, frames.bin at the sparse/ root) we don't need.
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

    # TXT mirror for grep-ability; STG's reader prefers .bin (dataset_readers.py:213)
    # so keeping both costs a tiny bit of disk and helps humans.
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

    # Sanity-check the deliverable before signalling done.
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

    print(f"[colmap-triangulate] done → {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
