#!/usr/bin/env python3
"""Standard-library implementation of builtin/slice."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Interval slice of arrayed<T>.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--end", type=int, default=-1)
    parser.add_argument("--step", type=int, default=1)
    args = parser.parse_args()
    if not args.input.is_dir():
        print(f"ERROR: input is not a directory: {args.input}", file=sys.stderr)
        return 1
    if args.step < 1:
        print(f"ERROR: step must be >= 1 (got {args.step})", file=sys.stderr)
        return 2
    if args.start < 0:
        print(f"ERROR: start must be >= 0 (got {args.start})", file=sys.stderr)
        return 2
    if args.end != -1 and args.end < 0:
        print(f"ERROR: end must be -1 (=to end) or >= 0 (got {args.end})", file=sys.stderr)
        return 2
    entries = sorted(item.name for item in args.input.iterdir() if not item.name.startswith("."))
    if not entries:
        print(f"ERROR: input has 0 elements — nothing to slice ({args.input})", file=sys.stderr)
        return 1
    end = len(entries) if args.end == -1 else min(args.end, len(entries))
    start = min(args.start, len(entries))
    picked = entries[start : end : args.step]
    if not picked:
        print("ERROR: empty slice", file=sys.stderr)
        return 1
    args.output.mkdir(parents=True, exist_ok=True)
    for name in picked:
        target = args.output / name
        if target.is_symlink() or (target.exists() and not target.is_dir()):
            target.unlink()
        elif target.is_dir():
            continue
        os.symlink((args.input / name).resolve(), target)
    print(f"slice: picked {len(picked)}/{len(entries)} elements")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
