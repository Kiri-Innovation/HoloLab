#!/usr/bin/env python3
"""Transpose arrayed<frame_sequence> from (camera × frame) to (frame × camera).

Input layout:  ``<input>/<cam_id>/frames/frame_XXXXXX.<ext>``
Output layout: ``<output>/<frame_key>/frames/<cam_id>.<ext>``

``frame_key`` is the frame filename stem preserved verbatim (e.g.
``frame_000000``), so element order lines up with the source frame indices.

Symlinks — never copies — so 21 cams × 400 frames stays cheap on disk.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True, help="arrayed<frame_sequence> input root")
    ap.add_argument("--output", required=True, help="arrayed<frame_sequence> output root")
    args = ap.parse_args()

    input_root = Path(args.input)
    output_root = Path(args.output)

    if not input_root.is_dir():
        print(f"ERROR: input is not a directory: {input_root}", file=sys.stderr)
        return 1

    output_root.mkdir(parents=True, exist_ok=True)

    cam_dirs = sorted(d for d in input_root.iterdir() if d.is_dir())
    if not cam_dirs:
        print(f"ERROR: no camera subdirs under {input_root}", file=sys.stderr)
        return 1

    total_links = 0
    frames_seen: set[str] = set()

    for cam_dir in cam_dirs:
        cam_id = cam_dir.name
        frames_dir = cam_dir / "frames"
        if not frames_dir.is_dir():
            print(f"WARN: no frames/ under {cam_dir}, skipping", file=sys.stderr)
            continue
        for frame_file in sorted(frames_dir.iterdir()):
            if not frame_file.is_file():
                continue
            frame_key = frame_file.stem  # e.g. "frame_000000"
            out_frame_dir = output_root / frame_key / "frames"
            out_frame_dir.mkdir(parents=True, exist_ok=True)
            dest = out_frame_dir / f"{cam_id}{frame_file.suffix}"
            # Rewire an existing symlink cleanly (allows idempotent re-runs).
            if dest.is_symlink() or dest.exists():
                dest.unlink()
            dest.symlink_to(frame_file.resolve())
            frames_seen.add(frame_key)
            total_links += 1

    print(
        f"regroup-by-frame: {len(cam_dirs)} cams × {len(frames_seen)} frames "
        f"= {total_links} symlinks"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
