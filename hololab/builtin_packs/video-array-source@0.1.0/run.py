#!/usr/bin/env python3
"""Build an arrayed video-source handle from a top-level video directory."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not args.video_dir.is_dir():
        print(f"video_dir not found or not a directory: {args.video_dir}", file=sys.stderr)
        return 2

    args.output.mkdir(parents=True, exist_ok=True)
    videos = sorted(
        (
            path
            for path in args.video_dir.iterdir()
            if not path.name.startswith(".")
            and (path.is_file() or path.is_symlink())
            and path.suffix.lower() in VIDEO_EXTENSIONS
        ),
        key=lambda path: os.fsencode(path.name),
    )
    for source in videos:
        element = args.output / source.stem
        element.mkdir(parents=True, exist_ok=True)
        target = element / source.name
        if target.is_symlink() or target.is_file():
            target.unlink()
        target.symlink_to(source)
    if not videos:
        print(
            f"no video files (.mp4/.mov/.mkv/.webm/.avi/.m4v) found in {args.video_dir}",
            file=sys.stderr,
        )
        return 2
    print(f"[ITER 1/1] arrayed {len(videos)} video(s) into element subdirs of {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
