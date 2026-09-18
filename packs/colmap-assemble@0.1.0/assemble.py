#!/usr/bin/env python3
"""Assemble arrayed<colmap-cams> + arrayed<colmap-points> + arrayed<frame_sequence>
into the container-of-``colmap_N/`` layout STG consumes.

Layout produced::

    output/
      colmap_0/
        sparse/0/
          cameras.txt   -> {cams}/<elem_0>/cameras.txt
          images.txt    -> {cams}/<elem_0>/images.txt
          points3D.txt  -> {points}/<elem_0>/points3D.txt
        images/
          <cam>.png     -> {frames}/<elem_0>/frames/<cam>.png
      colmap_1/
        ...

Element ids across the three inputs must agree — assembler asserts on load.
Element order = sorted subdirectory order (matches the framework's fan-out
element ordering).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _element_ids(root: Path, label: str) -> list[str]:
    if not root.is_dir():
        print(f"ERROR: {label} input is not a directory: {root}", file=sys.stderr)
        sys.exit(2)
    return sorted(
        e.name for e in root.iterdir() if not e.name.startswith(".") and e.is_dir()
    )


def _symlink(dest: Path, target: Path) -> None:
    if dest.is_symlink() or dest.exists():
        dest.unlink()
    dest.symlink_to(target.resolve())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cams", required=True)
    ap.add_argument("--points", required=True)
    ap.add_argument("--frames", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    cams_root = Path(args.cams)
    points_root = Path(args.points)
    frames_root = Path(args.frames)
    output = Path(args.output)

    cam_elems = _element_ids(cams_root, "cams")
    pt_elems = _element_ids(points_root, "points")
    fr_elems = _element_ids(frames_root, "frames")

    if cam_elems != pt_elems or cam_elems != fr_elems:
        print(
            "ERROR: arrayed inputs disagree on element set:\n"
            f"  cams   = {cam_elems}\n"
            f"  points = {pt_elems}\n"
            f"  frames = {fr_elems}",
            file=sys.stderr,
        )
        return 3

    if not cam_elems:
        print("ERROR: no elements found in any input", file=sys.stderr)
        return 3

    output.mkdir(parents=True, exist_ok=True)

    for i, elem in enumerate(cam_elems):
        colmap_dir = output / f"colmap_{i}"
        sparse = colmap_dir / "sparse" / "0"
        sparse.mkdir(parents=True, exist_ok=True)
        images_dir = colmap_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        # sparse/0 — cameras + images + points3D (all .txt from our packs).
        cam_src = cams_root / elem
        pt_src = points_root / elem
        for name in ("cameras.txt", "images.txt"):
            src = cam_src / name
            if not src.exists():
                print(f"ERROR: missing {name} under {cam_src}", file=sys.stderr)
                return 4
            _symlink(sparse / name, src)
        pt3d = pt_src / "points3D.txt"
        if not pt3d.exists():
            print(f"ERROR: missing points3D.txt under {pt_src}", file=sys.stderr)
            return 4
        _symlink(sparse / "points3D.txt", pt3d)

        # images/ — each cam's image at this frame (from regroup-by-frame's
        # output shape: {elem}/frames/<cam>.<ext>).
        fr_src = frames_root / elem / "frames"
        if not fr_src.is_dir():
            print(f"WARN: no frames/ under {fr_src}, images/ will be empty", file=sys.stderr)
        else:
            for img in sorted(fr_src.iterdir()):
                if not img.is_file() and not img.is_symlink():
                    continue
                _symlink(images_dir / img.name, img)

    # Idempotency marker.
    (output / ".hololab-done").touch()
    print(f"colmap-assemble: wrote {len(cam_elems)} colmap_N/ subdirs → {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
