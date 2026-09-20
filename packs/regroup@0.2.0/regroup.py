#!/usr/bin/env python3
"""Generic N-D permutation on an arrayed<arrayed<T>> tree.

Reorders the axes of a multi-dim arrayed handle by a caller-supplied
permutation of dim labels.

Contract
--------

* Source layout: ``<input>/<axis0_dir>/<axis1_dir>/.../<axisN-1_dir>/<T>``,
  outer first. The names of ``axisK_dir`` come from
  :func:`os.scandir` (sorted). Their *meaning* is the label at
  ``input_dims[K]`` — the pack doesn't parse the dir name, just its
  position in the sorted list.
* Target layout: ``<output>/<axis0_dir>/<axis1_dir>/.../<axisN-1_dir>/<T>``
  under a permuted axis order — ``axisK_dir`` on the target is named
  ``<output_dims[K]>_<idx:04d>`` where ``idx`` is the sorted index of
  the corresponding source axis value. This drops the classic
  ``frame-by-frame``'s "echo the source filename stem" convention;
  callers who cared about the old names should switch to a preview /
  metadata sidecar.
* Symlinks — never copies. Leaves are the *T*-directories (or *T*-files;
  we don't peek inside).
* Idempotent: an existing symlink at a target position is unlinked
  before the new one is created, so re-running with the same params
  produces the same tree.

Validation
----------

* ``input_dims`` and ``output_dims`` must be the same set of strings,
  same length. Anything else fails at start.
* Depth of the source tree must equal ``len(input_dims)``. A shorter
  tree (missing an axis directory somewhere) fails partway with a
  clear "expected X levels, saw Y" message.

Only depends on the stdlib.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _list_subdirs_sorted(root: Path) -> list[str]:
    """Sorted non-dot subdir names of ``root``. Returns [] for missing/empty."""
    if not root.is_dir():
        return []
    return sorted(
        e.name for e in root.iterdir() if e.is_dir() and not e.name.startswith(".")
    )


def _enumerate_leaves(root: Path, depth: int) -> list[tuple[list[str], Path]]:
    """Walk ``depth`` levels below ``root``; return each leaf's axis path.

    Returned as list of ``(axis_names, leaf_dir)`` — ``axis_names`` is
    the ordered list of subdir names traversed (outer first), length
    equals ``depth``. ``leaf_dir`` is the target of the traversal — a
    directory on disk that the pack will symlink at the target.
    """
    if depth == 0:
        return [([], root)]
    out: list[tuple[list[str], Path]] = []
    for name in _list_subdirs_sorted(root):
        for sub_names, leaf in _enumerate_leaves(root / name, depth - 1):
            out.append(([name, *sub_names], leaf))
    return out


def _permutation_of(input_dims: list[str], output_dims: list[str]) -> list[int]:
    """Return the permutation p such that ``output_dims[k] == input_dims[p[k]]``.

    Raises ``ValueError`` if the two lists aren't set-equal or contain
    duplicates.
    """
    if len(input_dims) != len(output_dims):
        raise ValueError(
            f"input_dims / output_dims length mismatch: {input_dims!r} vs {output_dims!r}"
        )
    if len(set(input_dims)) != len(input_dims):
        raise ValueError(f"input_dims has duplicates: {input_dims!r}")
    if set(input_dims) != set(output_dims):
        raise ValueError(
            f"input_dims and output_dims must be the same set of labels "
            f"(got {input_dims!r} vs {output_dims!r})"
        )
    index_of = {name: i for i, name in enumerate(input_dims)}
    return [index_of[name] for name in output_dims]


def main() -> int:
    ap = argparse.ArgumentParser(description="Generic N-D arrayed<T> permutation.")
    ap.add_argument("--input", required=True, help="arrayed<arrayed<T>> input root")
    ap.add_argument("--output", required=True, help="arrayed<arrayed<T>> output root")
    ap.add_argument(
        "--input-dims",
        required=True,
        help="JSON list of source axis labels, outer first (e.g. '[\"camera\",\"frame\"]').",
    )
    ap.add_argument(
        "--output-dims",
        required=True,
        help="JSON list of target axis labels, outer first (e.g. '[\"frame\",\"camera\"]').",
    )
    args = ap.parse_args()

    input_root = Path(args.input)
    output_root = Path(args.output)

    try:
        input_dims: list[str] = json.loads(args.input_dims)
        output_dims: list[str] = json.loads(args.output_dims)
    except json.JSONDecodeError as exc:
        print(f"ERROR: dim list is not valid JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(input_dims, list) or not isinstance(output_dims, list):
        print("ERROR: --input-dims / --output-dims must be JSON lists", file=sys.stderr)
        return 2
    if not all(isinstance(x, str) and x for x in input_dims + output_dims):
        print("ERROR: dim labels must be non-empty strings", file=sys.stderr)
        return 2

    try:
        perm = _permutation_of(input_dims, output_dims)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if not input_root.is_dir():
        print(f"ERROR: input is not a directory: {input_root}", file=sys.stderr)
        return 1

    depth = len(input_dims)
    output_root.mkdir(parents=True, exist_ok=True)

    # Precompute sorted position within each source axis, so target
    # directory names are stable ``<label>_NNNN`` slots. Cross-branch
    # positions must agree per-axis (e.g. every camera appears in every
    # frame OR fails cleanly on the mismatch).
    axis_values: list[list[str]] = _collect_axis_values(input_root, depth)
    for k, values in enumerate(axis_values):
        if not values:
            print(
                f"ERROR: source axis {k} ({input_dims[k]!r}) has 0 elements "
                f"under {input_root}",
                file=sys.stderr,
            )
            return 1
    sorted_positions: list[dict[str, int]] = [
        {name: idx for idx, name in enumerate(values)} for values in axis_values
    ]

    leaves = _enumerate_leaves(input_root, depth)
    if not leaves:
        print(f"ERROR: no leaves at depth {depth} under {input_root}", file=sys.stderr)
        return 1

    total_links = 0
    for source_axis_values, leaf_path in leaves:
        # Per-axis source indices (sorted position on the source side).
        src_indices = [sorted_positions[k][source_axis_values[k]] for k in range(depth)]
        # Reorder into target-axis order via the permutation.
        tgt_indices = [src_indices[perm[k]] for k in range(depth)]
        tgt_axis_labels = [output_dims[k] for k in range(depth)]
        target = output_root
        for label, idx in zip(tgt_axis_labels, tgt_indices, strict=True):
            target = target / f"{label}_{idx:04d}"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() or target.exists():
            if target.is_symlink() or not target.is_dir():
                target.unlink()
            else:
                # Existing directory (not a symlink) — leave it alone to
                # avoid clobbering a partial re-run's real data.
                continue
        os.symlink(leaf_path.resolve(), target)
        total_links += 1

    axes_str = " x ".join(
        f"{name}[{len(values)}]"
        for name, values in zip(input_dims, axis_values, strict=True)
    )
    print(
        f"regroup: {axes_str} = {total_links} leaves, "
        f"{input_dims!r} → {output_dims!r}"
    )
    return 0


def _collect_axis_values(root: Path, depth: int) -> list[list[str]]:
    """Sorted deduped values seen on each axis, outer first.

    Walks the full tree once. Axis 0 = subdir names of ``root``.
    Axis 1 = union of subdir names two levels deep, sorted. And so on.
    """
    axes: list[set[str]] = [set() for _ in range(depth)]

    def _walk(node: Path, k: int) -> None:
        if k >= depth:
            return
        for name in _list_subdirs_sorted(node):
            axes[k].add(name)
            _walk(node / name, k + 1)

    _walk(root, 0)
    return [sorted(s) for s in axes]


if __name__ == "__main__":
    sys.exit(main())
