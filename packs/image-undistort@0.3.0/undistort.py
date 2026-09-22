#!/usr/bin/env python3
"""Undistort one (intr, image) pair via COLMAP's ``image_undistorter``.

Scalar-per-invocation; caller fans out by wiring arrayed<intr> +
arrayed<image>. Both inputs and outputs use the new (2026-09-22) ``intr``
+ ``image`` types — a per-cam camera intrinsic and its images.

The image handle may carry a single file or a directory of files (a
per-cam frame sequence). Whichever shape is present is preserved on
the output side: N images in → N undistorted images out at the PINHOLE
resolution.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def stage_images(source_dir: Path, scratch_input: Path) -> list[str]:
    """Hardlink each per-cam file into ``scratch_input`` with a flat name."""
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


def _first_camera_id(cameras_txt: Path) -> int:
    for ln in cameras_txt.read_text().splitlines():
        if not ln or ln.startswith("#"):
            continue
        return int(ln.split()[0])
    raise SystemExit(f"no camera entry in {cameras_txt}")


def write_identity_prior(
    scratch_prior: Path, source_cameras_txt: Path, image_names: list[str]
) -> None:
    """image_undistorter needs a sparse model (cameras + images + points3D)."""
    if scratch_prior.exists():
        shutil.rmtree(scratch_prior)
    scratch_prior.mkdir(parents=True)
    shutil.copyfile(source_cameras_txt, scratch_prior / "cameras.txt")
    first_cid = _first_camera_id(source_cameras_txt)
    lines: list[str] = []
    for i, name in enumerate(image_names, start=1):
        lines.append(f"{i} 1 0 0 0 0 0 0 {first_cid} {name}\n\n")
    (scratch_prior / "images.txt").write_text("".join(lines))
    (scratch_prior / "points3D.txt").write_text("")


def run(cmd: list, step: str) -> None:
    print(f"[image-undistort/0.3] {step}", flush=True)
    subprocess.run([str(x) for x in cmd], check=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--intr", type=Path, required=True, help="intr handle (contains cameras.txt).")
    ap.add_argument(
        "--image",
        type=Path,
        required=True,
        help="image handle: file(s) at the handle root or under legacy frames/.",
    )
    ap.add_argument(
        "--intr-out", type=Path, required=True, help="Output intr handle (PINHOLE cameras.txt)."
    )
    ap.add_argument(
        "--image-out", type=Path, required=True, help="Output image handle (undistorted)."
    )
    ap.add_argument("--scratch", type=Path, required=True)
    ap.add_argument("--blank-pixels", type=int, default=0)
    args = ap.parse_args()

    source_cameras_txt = args.intr / "cameras.txt"
    if not source_cameras_txt.is_file():
        print(f"ERROR: missing cameras.txt at {source_cameras_txt}", file=sys.stderr)
        return 2

    # Image handle: prefer flat files at root; fall back to legacy frames/.
    if any(p.is_file() and not p.name.startswith(".") for p in args.image.iterdir()):
        images_dir = args.image
    else:
        images_dir = args.image / "frames"
    if not images_dir.is_dir():
        print(
            f"ERROR: image handle has no image files at root or frames/ subdir: {args.image}",
            file=sys.stderr,
        )
        return 2

    args.intr_out.mkdir(parents=True, exist_ok=True)
    args.image_out.mkdir(parents=True, exist_ok=True)
    args.scratch.mkdir(parents=True, exist_ok=True)

    scratch_input = args.scratch / "input"
    scratch_prior = args.scratch / "prior"
    scratch_output = args.scratch / "undistorter_out"
    if scratch_output.exists():
        shutil.rmtree(scratch_output)
    scratch_output.mkdir(parents=True)

    image_names = stage_images(images_dir, scratch_input)
    write_identity_prior(scratch_prior, source_cameras_txt, image_names)

    run(
        [
            "colmap",
            "image_undistorter",
            "--image_path",
            scratch_input,
            "--input_path",
            scratch_prior,
            "--output_path",
            scratch_output,
            "--output_type",
            "COLMAP",
            "--blank_pixels",
            args.blank_pixels,
        ],
        f"image_undistorter (N={len(image_names)}, blank_pixels={args.blank_pixels})",
    )

    sparse_root = scratch_output / "sparse"
    if not sparse_root.is_dir():
        print(f"ERROR: image_undistorter produced no sparse/: {sparse_root}", file=sys.stderr)
        return 3
    sparse_0 = sparse_root / "0"
    sparse_0.mkdir(exist_ok=True)
    for name in ("cameras.bin", "images.bin", "points3D.bin"):
        src = sparse_root / name
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

    pinhole_cameras = sparse_0 / "cameras.txt"
    if not pinhole_cameras.is_file():
        print(f"ERROR: expected PINHOLE cameras.txt at {pinhole_cameras}", file=sys.stderr)
        return 3
    shutil.copyfile(pinhole_cameras, args.intr_out / "cameras.txt")

    out_root = args.image_out
    legacy_frames = out_root / "frames"
    if legacy_frames.is_dir():
        shutil.rmtree(legacy_frames)
    for stale in out_root.glob("*"):
        if stale.is_file() and not stale.name.startswith("."):
            stale.unlink()
    out_root.mkdir(parents=True, exist_ok=True)
    src_images = scratch_output / "images"
    n_out = 0
    for src in sorted(src_images.iterdir()):
        if not src.is_file():
            continue
        dst = out_root / src.name
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)
        n_out += 1
    if n_out != len(image_names):
        print(
            f"ERROR: undistorter produced {n_out} images, expected {len(image_names)}",
            file=sys.stderr,
        )
        return 3
    print(f"[image-undistort/0.3] done → PINHOLE cameras.txt + {n_out} undistorted images")
    return 0


if __name__ == "__main__":
    sys.exit(main())
