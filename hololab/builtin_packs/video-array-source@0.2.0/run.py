"""Import one directory snapshot as an anonymous ordered video list."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from hololab.array_positions import encode_position

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


def build_array(source_dir: Path, output: Path) -> int:
    candidates = [
        p
        for p in source_dir.iterdir()
        if not p.name.startswith(".") and p.suffix.lower() in VIDEO_EXTENSIONS
    ]
    for source in candidates:
        if not source.is_file():
            raise ValueError(
                f"video is not a readable regular file (or is a broken link): {source}"
            )
        with source.open("rb") as stream:
            stream.read(1)
    candidates.sort(key=lambda p: os.fsencode(p.name))
    if not candidates:
        raise ValueError(f"no supported videos in {source_dir}")
    if output.exists() and any(output.iterdir()):
        raise ValueError("video array output must be empty")
    output.mkdir(parents=True, exist_ok=True)
    for index, source in enumerate(candidates):
        element = output / encode_position(index)
        element.mkdir()
        (element / ("input" + source.suffix)).symlink_to(source.resolve(strict=True))
    return len(candidates)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        count = build_array(args.video_dir, args.output)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"[ITER 1/1] arrayed {count} video(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
