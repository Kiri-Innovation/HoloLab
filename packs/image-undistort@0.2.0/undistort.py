#!/usr/bin/env python3
"""Undistort one (pinhole_intrinsics, distortion, images) triple via COLMAP image_undistorter.

Inputs are the three typed outputs of ``colmap-split@0.2.0``:
* ``pinhole_intrinsics`` — COLMAP PINHOLE cameras.txt (fx fy cx cy only).
* ``distortion`` — JSON with distortion coefficients + model name
  (``distortion.json``).
* ``images`` — frame_sequence handle (frames/ subdir).

Internally this pack reassembles the full OPENCV (or other) cameras.txt
before handing it to ``colmap image_undistorter``, then publishes the
PINHOLE cameras.txt the undistorter emits and the undistorted images.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Mirrors colmap-split@0.2.0 — number of pinhole params per model.
_PINHOLE_N: dict[str, int] = {
    "SIMPLE_PINHOLE": 3,
    "PINHOLE": 4,
    "SIMPLE_RADIAL": 3,
    "RADIAL": 3,
    "OPENCV": 4,
    "SIMPLE_OPENCV": 3,
    "OPENCV_FISHEYE": 4,
    "FULL_OPENCV": 4,
    "FOV": 4,
    "THIN_PRISM_FISHEYE": 4,
}
_SINGLE_FOCAL: frozenset[str] = frozenset(
    {"SIMPLE_PINHOLE", "SIMPLE_RADIAL", "RADIAL", "SIMPLE_OPENCV"}
)


def reassemble_cameras_txt(
    pinhole_txt: Path, distortion_json: Path, dest: Path
) -> None:
    """Reconstruct the original cameras.txt from PINHOLE intrinsics + distortion JSON.

    Inverse of ``colmap-split@0.2.0::split_cameras_txt``. Writes a valid
    COLMAP cameras.txt at ``dest`` with the original model and all params.
    """
    pinhole: dict[int, tuple[int, int, float, float, float, float]] = {}
    for ln in pinhole_txt.read_text().splitlines():
        if not ln or ln.startswith("#"):
            continue
        parts = ln.split()
        cam_id = int(parts[0])
        w, h = int(parts[2]), int(parts[3])
        fx, fy, cx, cy = float(parts[4]), float(parts[5]), float(parts[6]), float(parts[7])
        pinhole[cam_id] = (w, h, fx, fy, cx, cy)

    dist_data = json.loads(distortion_json.read_text())
    dist_by_id = {cam["camera_id"]: cam for cam in dist_data["cameras"]}

    lines: list[str] = []
    for cam_id in sorted(pinhole):
        w, h, fx, fy, cx, cy = pinhole[cam_id]
        dist = dist_by_id[cam_id]
        model = dist["model"]
        dist_params = dist["params"]

        ph_str = f"{fx} {cx} {cy}" if model in _SINGLE_FOCAL else f"{fx} {fy} {cx} {cy}"

        if dist_params:
            dist_str = " ".join(str(p) for p in dist_params)
            lines.append(f"{cam_id} {model} {w} {h} {ph_str} {dist_str}")
        else:
            lines.append(f"{cam_id} {model} {w} {h} {ph_str}")

    dest.write_text("\n".join(lines) + "\n")


def stage_images(source_dir: Path, scratch_input: Path) -> list[str]:
    """Hardlink (fallback copy) each per-cam file into ``scratch_input`` with flat names.

    COLMAP resolves symlinks while walking ``--image_path`` (verified by
    colmap-triangulate@0.2.0), which pollutes stored image names. Hardlink
    into a clean scratch dir avoids that.
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


def _first_camera_id(cameras_txt: Path) -> int:
    for ln in cameras_txt.read_text().splitlines():
        if not ln or ln.startswith("#"):
            continue
        return int(ln.split()[0])
    raise SystemExit(f"no camera entry in {cameras_txt}")


def run(cmd: list, step: str) -> None:
    print(f"[image-undistort] {step}", flush=True)
    subprocess.run([str(x) for x in cmd], check=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--pinhole-intrinsics",
        type=Path,
        required=True,
        help="colmap-cameras-txt handle with PINHOLE cameras.txt.",
    )
    ap.add_argument(
        "--distortion",
        type=Path,
        required=True,
        help="camera-distortion-json handle with distortion.json.",
    )
    ap.add_argument(
        "--images",
        type=Path,
        required=True,
        help="frame_sequence handle (frames/ subdir).",
    )
    ap.add_argument("--cameras-out", type=Path, required=True)
    ap.add_argument("--undistorted-out", type=Path, required=True)
    ap.add_argument("--scratch", type=Path, required=True)
    ap.add_argument("--blank-pixels", type=int, default=0)
    args = ap.parse_args()

    source_pinhole_txt = args.pinhole_intrinsics / "cameras.txt"
    distortion_json_path = args.distortion / "distortion.json"
    images_dir = args.images / "frames"

    if not source_pinhole_txt.is_file():
        print(f"ERROR: missing cameras.txt at {source_pinhole_txt}", file=sys.stderr)
        return 2
    if not distortion_json_path.is_file():
        print(f"ERROR: missing distortion.json at {distortion_json_path}", file=sys.stderr)
        return 2
    if not images_dir.is_dir():
        print(f"ERROR: images handle missing frames/ subdir: {images_dir}", file=sys.stderr)
        return 2

    args.cameras_out.mkdir(parents=True, exist_ok=True)
    args.undistorted_out.mkdir(parents=True, exist_ok=True)
    args.scratch.mkdir(parents=True, exist_ok=True)

    scratch_input = args.scratch / "input"
    scratch_prior = args.scratch / "prior"
    scratch_output = args.scratch / "undistorter_out"
    if scratch_prior.exists():
        shutil.rmtree(scratch_prior)
    scratch_prior.mkdir(parents=True)
    if scratch_output.exists():
        shutil.rmtree(scratch_output)
    scratch_output.mkdir(parents=True)

    image_names = stage_images(images_dir, scratch_input)

    # Reassemble full camera model for COLMAP image_undistorter.
    assembled_cameras = scratch_prior / "cameras.txt"
    reassemble_cameras_txt(source_pinhole_txt, distortion_json_path, assembled_cameras)

    first_cid = _first_camera_id(assembled_cameras)
    lines = [
        f"{i} 1 0 0 0 0 0 0 {first_cid} {name}\n\n"
        for i, name in enumerate(image_names, start=1)
    ]
    (scratch_prior / "images.txt").write_text("".join(lines))
    (scratch_prior / "points3D.txt").write_text("")

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
    shutil.copyfile(pinhole_cameras, args.cameras_out / "cameras.txt")

    out_frames = args.undistorted_out / "frames"
    if out_frames.exists():
        shutil.rmtree(out_frames)
    out_frames.mkdir(parents=True)
    src_images = scratch_output / "images"
    n_out = 0
    for src in sorted(src_images.iterdir()):
        if not src.is_file():
            continue
        dst = out_frames / src.name
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
    print(f"[image-undistort] done → PINHOLE cameras.txt + {n_out} undistorted images")
    return 0


if __name__ == "__main__":
    sys.exit(main())
