#!/usr/bin/env python3
"""Transpose arrayed<frame_sequence> from (camera x frame) to (frame x camera)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def enumerate_arrayed_elements(root: Path) -> list[Path]:
    """Return canonical immediate elements: sorted non-dot directories."""
    return sorted(d for d in root.iterdir() if not d.name.startswith(".") and d.is_dir())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True, help="arrayed<frame_sequence> input root")
    ap.add_argument("--output", required=True, help="arrayed<frame_sequence> output root")
    args = ap.parse_args()
    input_root, output_root = Path(args.input), Path(args.output)
    if not input_root.is_dir():
        print(f"ERROR: input is not a directory: {input_root}", file=sys.stderr)
        return 1
    output_root.mkdir(parents=True, exist_ok=True)
    cam_dirs = enumerate_arrayed_elements(input_root)
    if not cam_dirs:
        print(f"ERROR: no camera subdirs under {input_root}", file=sys.stderr)
        return 1
    total_links, frames_seen = 0, set()
    for cam_dir in cam_dirs:
        frames_dir = cam_dir / "frames"
        if not frames_dir.is_dir():
            print(f"WARN: no frames/ under {cam_dir}, skipping", file=sys.stderr)
            continue
        for frame_file in sorted(frames_dir.iterdir()):
            if not frame_file.is_file() or frame_file.name.startswith("."):
                continue
            out_frame_dir = output_root / frame_file.stem / "frames"
            out_frame_dir.mkdir(parents=True, exist_ok=True)
            dest = out_frame_dir / f"{cam_dir.name}{frame_file.suffix}"
            if dest.is_symlink() or dest.exists():
                dest.unlink()
            dest.symlink_to(frame_file.resolve())
            frames_seen.add(frame_file.stem)
            total_links += 1
    print(
        f"regroup-by-frame: {len(cam_dirs)} cams x {len(frames_seen)} frames = {total_links} symlinks"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
