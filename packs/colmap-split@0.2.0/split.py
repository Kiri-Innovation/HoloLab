#!/usr/bin/env python3
"""Split a colmap-cams into three typed outputs.

* ``pinhole_intrinsics`` — COLMAP PINHOLE cameras.txt (fx fy cx cy only;
  distortion stripped). Valid COLMAP text; no proprietary schema.
* ``distortion`` — JSON blob with the distortion coefficients + model name
  needed to reassemble the original camera (the *only* output that is not
  COLMAP-native; COLMAP has no single-file format for distortion alone).
* ``poses`` — COLMAP images.txt with 2D observations blanked (poses only).

Reassembly invariant: pinhole (fx fy cx cy) + distortion (params in original
COLMAP order) reconstruct the original cameras.txt entry byte-for-byte in
numeric value. Models with a single focal length (SIMPLE_RADIAL, RADIAL,
SIMPLE_OPENCV, SIMPLE_PINHOLE) are normalised to PINHOLE (fx = fy = f).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Number of pinhole params (fx[,fy] cx cy) before the distortion tail.
# 3-param models use a single focal length f written as fx=fy=f in PINHOLE.
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


def split_cameras_txt(
    src: Path, pinhole_out: Path, distortion_out: Path
) -> int:
    """Parse cameras.txt; write PINHOLE cameras.txt + distortion JSON.

    Returns the number of cameras written.
    """
    pinhole_lines: list[str] = []
    dist_cameras: list[dict] = []

    for ln in src.read_text().splitlines():
        if not ln or ln.startswith("#"):
            continue
        parts = ln.split()
        cam_id = int(parts[0])
        model = parts[1]
        w, h = int(parts[2]), int(parts[3])
        params = [float(p) for p in parts[4:]]

        n_ph = _PINHOLE_N.get(model)
        if n_ph is None:
            print(f"ERROR: unsupported camera model {model!r}", file=sys.stderr)
            raise SystemExit(2)

        ph = params[:n_ph]
        dist = params[n_ph:]

        if model in _SINGLE_FOCAL:
            f, cx, cy = ph
            fx, fy = f, f
        else:
            fx, fy, cx, cy = ph

        pinhole_lines.append(f"{cam_id} PINHOLE {w} {h} {fx} {fy} {cx} {cy}")
        dist_cameras.append({"camera_id": cam_id, "model": model, "params": dist})

    pinhole_out.write_text("\n".join(pinhole_lines) + "\n")
    distortion_out.write_text(
        json.dumps({"schema_version": 1, "cameras": dist_cameras}, indent=2) + "\n"
    )
    return len(dist_cameras)


def strip_observations(source: Path, dest: Path) -> tuple[int, int]:
    """Rewrite images.txt keeping pose headers, blanking obs lines.

    Identical to colmap-split@0.1.0 — strict interleave cadence.
    """
    lines: list[str] = []
    kept_headers = 0
    it = iter(source.read_text().splitlines())
    for ln in it:
        if not ln or ln.startswith("#"):
            lines.append(ln)
            continue
        lines.append(ln)
        lines.append("")
        kept_headers += 1
        try:
            next(it)
        except StopIteration:
            break
    dest.write_text("\n".join(lines) + "\n")
    return kept_headers, len(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cams", type=Path, required=True)
    ap.add_argument("--pinhole-intrinsics-out", type=Path, required=True)
    ap.add_argument("--distortion-out", type=Path, required=True)
    ap.add_argument("--poses-out", type=Path, required=True)
    args = ap.parse_args()

    src_cameras = args.cams / "cameras.txt"
    src_images = args.cams / "images.txt"
    if not src_cameras.is_file() or not src_images.is_file():
        print(
            f"ERROR: input cams handle missing cameras.txt / images.txt under {args.cams}",
            file=sys.stderr,
        )
        return 2

    args.pinhole_intrinsics_out.mkdir(parents=True, exist_ok=True)
    args.distortion_out.mkdir(parents=True, exist_ok=True)
    args.poses_out.mkdir(parents=True, exist_ok=True)

    n = split_cameras_txt(
        src_cameras,
        args.pinhole_intrinsics_out / "cameras.txt",
        args.distortion_out / "distortion.json",
    )
    kept, _ = strip_observations(src_images, args.poses_out / "images.txt")

    print(
        f"colmap-split: {n} camera(s) split to PINHOLE+distortion; {kept} pose header(s) written"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
