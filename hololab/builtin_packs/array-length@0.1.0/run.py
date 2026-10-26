"""Standard-library implementation of builtin/array-length."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    count = sum(
        1
        for entry in os.scandir(args.input)
        if not entry.name.startswith(".")
        and (entry.is_dir(follow_symlinks=True) or entry.is_file(follow_symlinks=True))
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(f"{count}\n", encoding="utf-8")


if __name__ == "__main__":
    main()
