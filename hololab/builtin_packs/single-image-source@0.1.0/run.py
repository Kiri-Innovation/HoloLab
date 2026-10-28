#!/usr/bin/env python3
"""Publish one source image as a symlink using only the standard library."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image-path", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    source = args.image_path
    if not source.is_file():
        print(f"image_path not found or not a file: {source}", file=sys.stderr)
        return 2
    if source.suffix.lower() not in IMAGE_EXTENSIONS:
        print(
            f"image_path must end in .png, .jpg, .jpeg, or .webp: {source}",
            file=sys.stderr,
        )
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.is_symlink() or args.output.is_file():
        args.output.unlink()
    args.output.symlink_to(source)
    print("[ITER 1/1] source ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
