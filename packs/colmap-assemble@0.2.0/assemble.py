#!/usr/bin/env python3
"""Assemble ``arrayed<colmap-frame>`` into the ``colmap_N/`` container layout STG consumes.

Layout produced::

    output/
      colmap_0/
        sparse/0/       -> {frames}/<elem_0>/sparse/0/
        images/         -> {frames}/<elem_0>/images/
      colmap_1/
        ...

Element order = sorted subdirectory order (matches the framework's fan-out
ordering). All contents are symlinks — no bytes copied.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _element_ids(root: Path, label: str) -> list[str]:
    if not root.is_dir():
        print(f"ERROR: {label} input is not a directory: {root}", file=sys.stderr)
        sys.exit(2)
    return sorted(e.name for e in root.iterdir() if not e.name.startswith(".") and e.is_dir())


def _symlink(dest: Path, target: Path) -> None:
    if dest.is_symlink() or dest.exists():
        dest.unlink()
    dest.symlink_to(target.resolve())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--frames",
        required=True,
        help="arrayed<colmap-frame> root — one per-frame COLMAP dir per element.",
    )
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    frames_root = Path(args.frames)
    output = Path(args.output)

    elems = _element_ids(frames_root, "frames")
    if not elems:
        print(f"ERROR: no elements found under {frames_root}", file=sys.stderr)
        return 3

    output.mkdir(parents=True, exist_ok=True)

    for i, elem in enumerate(elems):
        colmap_dir = output / f"colmap_{i}"
        colmap_dir.mkdir(parents=True, exist_ok=True)

        src = frames_root / elem
        sparse_src = src / "sparse" / "0"
        images_src = src / "images"
        if not sparse_src.is_dir():
            print(f"ERROR: element {elem!r} missing sparse/0/: {sparse_src}", file=sys.stderr)
            return 4
        if not images_src.is_dir():
            print(f"ERROR: element {elem!r} missing images/: {images_src}", file=sys.stderr)
            return 4

        # colmap_i/sparse/0 → <elem>/sparse/0 (whole dir)
        (colmap_dir / "sparse").mkdir(parents=True, exist_ok=True)
        _symlink(colmap_dir / "sparse" / "0", sparse_src)
        # colmap_i/images → <elem>/images (whole dir)
        _symlink(colmap_dir / "images", images_src)

    (output / ".hololab-done").touch()
    print(f"colmap-assemble: wrote {len(elems)} colmap_N/ subdirs → {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
