#!/usr/bin/env python3
"""Interval selection on the outer dim of an ``arrayed<T>`` handle.

Enumerates the immediate children of the input dir in ``sorted(os.listdir)``
order — matching the framework's fan-out element order and ``get-index``'s
pick order — takes ``[start:end:step]`` of that list, and symlinks each
selected child under its **original name** into the output dir.

Only depends on the stdlib.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _list_sorted_children(root: Path) -> list[str]:
    """Sorted non-dot immediate children of ``root`` (files and dirs)."""
    return sorted(e.name for e in root.iterdir() if not e.name.startswith("."))


def _resolve_bounds(n: int, start: int, end: int, step: int) -> tuple[int, int, int]:
    """Normalize ``start`` / ``end`` / ``step`` against ``n`` elements.

    * ``end == -1`` → ``n`` ("to the end").
    * ``start`` and ``end`` are clamped to ``[0, n]``.
    * ``step`` must be ``>= 1``.

    Returns the concrete triple used for ``range(start, end, step)``.
    """
    if step < 1:
        raise ValueError(f"step must be >= 1 (got {step})")
    if start < 0:
        raise ValueError(f"start must be >= 0 (got {start})")
    if end != -1 and end < 0:
        raise ValueError(f"end must be -1 (=to end) or >= 0 (got {end})")

    resolved_end = n if end == -1 else min(end, n)
    resolved_start = min(start, n)
    return resolved_start, resolved_end, step


def main() -> int:
    ap = argparse.ArgumentParser(description="Interval slice of arrayed<T>.")
    ap.add_argument("--input", required=True, help="arrayed<T> input root")
    ap.add_argument("--output", required=True, help="arrayed<T> output root")
    ap.add_argument("--start", type=int, default=0, help="Inclusive start (>= 0).")
    ap.add_argument("--end", type=int, default=-1, help="Exclusive end. -1 = to the end (N).")
    ap.add_argument("--step", type=int, default=1, help="Stride (>= 1).")
    args = ap.parse_args()

    input_root = Path(args.input)
    output_root = Path(args.output)

    if not input_root.is_dir():
        print(f"ERROR: input is not a directory: {input_root}", file=sys.stderr)
        return 1

    entries = _list_sorted_children(input_root)
    n = len(entries)
    if n == 0:
        print(
            f"ERROR: input has 0 elements — nothing to slice ({input_root})",
            file=sys.stderr,
        )
        return 1

    try:
        s, e, st = _resolve_bounds(n, args.start, args.end, args.step)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    picked_indices = list(range(s, e, st))
    if not picked_indices:
        print(
            f"ERROR: empty slice — start={args.start} end={args.end} step={args.step} "
            f"resolved to range({s}, {e}, {st}) over N={n}",
            file=sys.stderr,
        )
        return 1

    output_root.mkdir(parents=True, exist_ok=True)

    for i in picked_indices:
        name = entries[i]
        src = (input_root / name).resolve()
        dst = output_root / name
        # Idempotent: unlink stale link/file, leave real dirs alone
        # (may hold partial re-run data — same policy as regroup).
        if dst.is_symlink() or (dst.exists() and not dst.is_dir()):
            dst.unlink()
        elif dst.is_dir():
            continue
        os.symlink(src, dst)

    print(
        f"slice: picked {len(picked_indices)}/{n} elements — "
        f"range({s}, {e}, {st}); "
        f"first='{entries[picked_indices[0]]}', last='{entries[picked_indices[-1]]}'"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
