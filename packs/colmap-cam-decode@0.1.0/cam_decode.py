#!/usr/bin/env python3
"""Explode a ``colmap-cams`` bundle into arrayed<pose> + arrayed<intr>.

Element ``i`` on both output arrays refers to the same physical camera
(sorted by NAME). Intrinsics are replicated to match pose cardinality.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_IMAGES_HDR = (
    "# Image list with two lines of data per image:\n"
    "#   IMAGE_ID, QW, QX, QY, QZ, TX, TY, TZ, CAMERA_ID, NAME\n"
    "#   POINTS2D[] as (X, Y, POINT3D_ID)\n"
    "# Number of images: 1\n"
)

_CAMERAS_HDR = (
    "# Camera list with one line of data per camera:\n"
    "#   CAMERA_ID, MODEL, WIDTH, HEIGHT, PARAMS[]\n"
    "# Number of cameras: 1\n"
)


def parse_cameras_txt(path: Path) -> dict[int, str]:
    """camera_id -> raw one-line camera row (verbatim, no trailing newline)."""
    out: dict[int, str] = {}
    for ln in path.read_text().splitlines():
        s = ln.rstrip("\n")
        if not s.strip() or s.lstrip().startswith("#"):
            continue
        cid = int(s.split()[0])
        out[cid] = s
    return out


def parse_images_txt(path: Path) -> list[tuple[int, str, int, str]]:
    """Return (image_id, pose_line, camera_id, name) per image entry.

    ``pose_line`` is the full COLMAP images.txt row verbatim (rewritten
    without observations by the emitter).
    """
    out: list[tuple[int, str, int, str]] = []
    lines = path.read_text().splitlines()
    it = iter(lines)
    for ln in it:
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split()
        if len(parts) < 10:
            continue
        iid = int(parts[0])
        cid = int(parts[8])
        name = parts[9]
        out.append((iid, s, cid, name))
        try:
            next(it)  # obs line
        except StopIteration:
            break
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cams", type=Path, required=True)
    ap.add_argument("--poses-out", type=Path, required=True)
    ap.add_argument("--intrs-out", type=Path, required=True)
    args = ap.parse_args()

    cams_txt = args.cams / "cameras.txt"
    imgs_txt = args.cams / "images.txt"
    if not cams_txt.is_file() or not imgs_txt.is_file():
        print(
            f"ERROR: cams input missing cameras.txt / images.txt under {args.cams}",
            file=sys.stderr,
        )
        return 2

    cameras = parse_cameras_txt(cams_txt)
    images = parse_images_txt(imgs_txt)
    if not images:
        print(f"ERROR: no image entries in {imgs_txt}", file=sys.stderr)
        return 3

    # Sort by NAME so element ordering is stable + matches on-disk cam dirs.
    # IMAGE_ID must not be used as an index — see docs in manifest.yaml.
    images.sort(key=lambda t: t[3])

    args.poses_out.mkdir(parents=True, exist_ok=True)
    args.intrs_out.mkdir(parents=True, exist_ok=True)

    # Wipe any stale element dirs from a re-run.
    for parent in (args.poses_out, args.intrs_out):
        for child in parent.iterdir():
            if child.is_dir() and not child.name.startswith("."):
                for f in child.iterdir():
                    if f.is_file():
                        f.unlink()
                child.rmdir()

    for i, (_iid, pose_line, cid, name) in enumerate(images):
        pose_dir = args.poses_out / f"pose_{i:04d}"
        intr_dir = args.intrs_out / f"intr_{i:04d}"
        pose_dir.mkdir(parents=True, exist_ok=True)
        intr_dir.mkdir(parents=True, exist_ok=True)

        # Rewrite IMAGE_ID as 1 so the single-row images.txt validates
        # against COLMAP's IMAGE_ID <= "Number of images" contract.
        parts = pose_line.split()
        parts[0] = "1"
        parts[8] = "1"  # local camera_id = 1
        (pose_dir / "images.txt").write_text(_IMAGES_HDR + " ".join(parts) + "\n\n")

        cam_row = cameras.get(cid)
        if cam_row is None:
            print(
                f"ERROR: image {name!r} references camera_id={cid} but no matching row in "
                f"cameras.txt (available: {sorted(cameras)})",
                file=sys.stderr,
            )
            return 4
        # Force local CAMERA_ID = 1 for the one-row extract, matching the
        # pose element's CAMERA_ID above.
        cam_parts = cam_row.split()
        cam_parts[0] = "1"
        (intr_dir / "cameras.txt").write_text(_CAMERAS_HDR + " ".join(cam_parts) + "\n")

    print(
        f"[colmap-cam-decode/0.1] done → {len(images)} pose + {len(images)} intr elements"
        f" (source: {len(cameras)} distinct intrinsics)",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
