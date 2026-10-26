#!/usr/bin/env python3
"""Standard-library implementation of builtin/arrayfy."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--count", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--element-id-format", default="%04d")
    args = parser.parse_args()
    try:
        count = int(args.count.read_text(encoding="utf-8").strip(), 10)
    except (OSError, ValueError) as exc:
        print(f"ERROR: count is not a base-10 integer: {exc}", file=sys.stderr)
        return 1
    if count < 0:
        print(f"ERROR: count must be non-negative (got {count})", file=sys.stderr)
        return 1
    args.output.mkdir(parents=True, exist_ok=True)
    source = args.data.resolve()
    try:
        names = [args.element_id_format % index for index in range(count)]
    except (TypeError, ValueError) as exc:
        print(f"ERROR: invalid element_id_format: {exc}", file=sys.stderr)
        return 2
    for name in names:
        target = args.output / name
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.exists():
            print(f"ERROR: output entry is a directory: {target}", file=sys.stderr)
            return 1
        os.symlink(source, target)
    print(f"arrayfy: replicated {source} → {count} elements as {args.element_id_format!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
