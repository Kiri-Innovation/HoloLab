#!/usr/bin/env python3
"""Permute explicit collection axes, with file OR directory leaves.

Every input_dims entry is one directory-container level. Image leaves are files,
not bundles with intrinsic dimensions. --content-dims is accepted for existing
job parameters but does not change enumeration depth or infer a data type.
Output leaves are symlinks; file extensions are preserved.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _enumerate_source_leaves(root: Path, dir_depth: int, content_dims: int):
    depth = dir_depth + content_dims
    out = []

    def walk(node, prefix):
        if len(prefix) == depth:
            out.append((prefix, node))
            return
        if not node.is_dir():
            raise ValueError(f"expected array container at {node}")
        for child in sorted(node.iterdir()):
            if not child.name.startswith("."):
                walk(child, [*prefix, child.name])

    walk(root, [])
    return out


def _collect_axis_values(root: Path, dir_depth: int, content_dims: int):
    axes = [set() for _ in range(dir_depth + content_dims)]
    for names, _ in _enumerate_source_leaves(root, dir_depth, content_dims):
        for i, name in enumerate(names):
            axes[i].add(name)
    return [sorted(a) for a in axes]


def _permutation_of(input_dims: list[str], output_dims: list[str]) -> list[int]:
    """Return the permutation ``p`` such that ``output_dims[k] == input_dims[p[k]]``.

    Raises ``ValueError`` on length mismatch, duplicates, or set-inequality.
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


def _target_path(
    output_root: Path,
    output_dims: list[str],
    tgt_indices: list[int],
    content_dims: int,
    leaf_ext: str,
) -> Path:
    """Name directory containers and preserve the final file leaf extension."""
    dir_depth = len(output_dims) - content_dims
    target = output_root
    for k in range(dir_depth):
        target = target / f"{output_dims[k]}_{tgt_indices[k]:04d}"
    if content_dims == 0:
        return target
    if content_dims == 1:
        label = output_dims[-1]
        idx = tgt_indices[-1]
        return target / f"{label}_{idx:04d}{leaf_ext}"
    raise NotImplementedError(f"content_dims={content_dims} not supported")


def main() -> int:
    ap = argparse.ArgumentParser(description="Generic N-D arrayed<T> permutation.")
    ap.add_argument("--input", required=True, help="arrayed<T> input root")
    ap.add_argument("--output", required=True, help="arrayed<T> output root")
    ap.add_argument(
        "--input-dims",
        required=True,
        help='JSON list of source axis labels, outer first (e.g. \'["cam","frame"]\').',
    )
    ap.add_argument(
        "--output-dims",
        required=True,
        help='JSON list of target axis labels, outer first (e.g. \'["frame","cam"]\').',
    )
    ap.add_argument(
        "--content-dims",
        type=int,
        default=0,
        help=("Legacy layout hint (0 or 1). All input_dims are explicit collections."),
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

    content_dims = args.content_dims
    if content_dims not in (0, 1):
        print(
            f"ERROR: --content-dims must be 0 or 1 (got {content_dims}); only "
            f"accepted layout hints are 0 and 1.",
            file=sys.stderr,
        )
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
    dir_depth = depth - content_dims
    if dir_depth < 0:
        print(
            f"ERROR: content_dims ({content_dims}) exceeds total dims ({depth})",
            file=sys.stderr,
        )
        return 2

    output_root.mkdir(parents=True, exist_ok=True)

    axis_values = _collect_axis_values(input_root, dir_depth, content_dims)
    for k, values in enumerate(axis_values):
        if not values:
            print(
                f"ERROR: source axis {k} ({input_dims[k]!r}) has 0 elements under {input_root}",
                file=sys.stderr,
            )
            return 1
    sorted_positions: list[dict[str, int]] = [
        {name: idx for idx, name in enumerate(values)} for values in axis_values
    ]

    leaves = _enumerate_source_leaves(input_root, dir_depth, content_dims)
    if not leaves:
        print(f"ERROR: no leaves found under {input_root}", file=sys.stderr)
        return 1

    total_links = 0
    for source_axis_values, leaf_path in leaves:
        src_indices = [sorted_positions[k][source_axis_values[k]] for k in range(depth)]
        tgt_indices = [src_indices[perm[k]] for k in range(depth)]
        leaf_ext = leaf_path.suffix if leaf_path.is_file() else ""
        target = _target_path(
            output_root, output_dims, tgt_indices, int(leaf_path.is_file()), leaf_ext
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() or target.exists():
            if target.is_symlink() or not target.is_dir():
                target.unlink()
            else:
                # Existing real directory — leave alone (partial re-run's data).
                continue
        os.symlink(leaf_path.resolve(), target)
        total_links += 1

    axes_str = " x ".join(
        f"{name}[{len(values)}]" for name, values in zip(input_dims, axis_values, strict=True)
    )
    kind = "explicit collection axes"
    print(f"regroup ({kind}): {axes_str} = {total_links} leaves, {input_dims!r} → {output_dims!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
