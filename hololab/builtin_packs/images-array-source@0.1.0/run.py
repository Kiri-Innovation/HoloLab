#!/usr/bin/env python3
"""Expose an existing directory of frames as a symlink-only image handle."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames-path", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    source: Path = args.frames_path
    if not source.is_dir():
        print(f"frames_path not found or not a directory: {source}", file=sys.stderr)
        return 2

    frames = sorted(
        (
            path
            for path in source.iterdir()
            if not path.name.startswith(".")
            and (path.is_file() or path.is_symlink())
            and path.suffix.lower() in IMAGE_EXTENSIONS
        ),
        key=lambda path: os.fsencode(path.name),
    )
    if not frames:
        print(
            f"no image files ({', '.join(sorted(IMAGE_EXTENSIONS))}) found in {source}",
            file=sys.stderr,
        )
        return 2

    output: Path = args.output
    output.mkdir(parents=True, exist_ok=True)
    for frame in frames:
        target = output / frame.name
        if target.is_symlink() or target.exists():
            target.unlink()
        target.symlink_to(frame.resolve())

    print(f"[ITER 1/1] linked {len(frames)} frame(s) from {source} into {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
