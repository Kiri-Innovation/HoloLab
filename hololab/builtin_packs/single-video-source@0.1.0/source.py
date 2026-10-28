"""Publish one source video without interpreting the filename as shell code."""

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    source = args.source
    if not source.is_file():
        print(f"video_path is not an existing file: {source}", file=sys.stderr)
        return 2
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    target = output / ("input" + source.suffix)
    if target.is_symlink():
        target.unlink()
    target.symlink_to(source.resolve())
    print("[ITER 1/1] source ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
