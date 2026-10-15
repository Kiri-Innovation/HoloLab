"""Publish one source video without interpreting the filename as shell code."""

import json
import sys
from pathlib import Path


def main():
    config = json.load(sys.stdin)
    source = Path(config["source"])
    if not source.is_file():
        raise ValueError(f"video_path is not an existing file: {source}")
    output = Path(config["output"])
    output.mkdir(parents=True, exist_ok=True)
    target = output / ("input" + source.suffix)
    if target.is_symlink():
        target.unlink()
    target.symlink_to(source.resolve())
    print("[ITER 1/1] source ready")


if __name__ == "__main__":
    main()
