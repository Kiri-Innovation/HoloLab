"""Standard-library implementation of builtin/get-index."""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path


def elements(directory: Path) -> list[Path]:
    return sorted(
        (
            Path(entry.path)
            for entry in os.scandir(directory)
            if not entry.name.startswith(".")
            and (entry.is_dir(follow_symlinks=True) or entry.is_file(follow_symlinks=True))
        ),
        key=lambda path: path.name,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--idx", required=True, type=int)
    args = parser.parse_args()
    entries = elements(args.input)
    if args.idx < 0 or args.idx >= len(entries):
        raise SystemExit(f"ERROR: idx {args.idx} out of range (array size {len(entries)})")
    if args.output.is_symlink() or args.output.is_file():
        args.output.unlink()
    elif args.output.exists():
        shutil.rmtree(args.output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.symlink_to(entries[args.idx].resolve())
    print(f"get-index: picked [{args.idx}] = {entries[args.idx].name}")


if __name__ == "__main__":
    main()
