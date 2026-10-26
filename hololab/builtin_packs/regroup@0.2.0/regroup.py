#!/usr/bin/env python3
"""Standard-library implementation of builtin/regroup."""

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
        for index, name in enumerate(names):
            axes[index].add(name)
    return [sorted(axis) for axis in axes]


def _permutation_of(input_dims: list[str], output_dims: list[str]) -> list[int]:
    if len(input_dims) != len(output_dims):
        raise ValueError(
            f"input_dims / output_dims length mismatch: {input_dims!r} vs {output_dims!r}"
        )
    if len(set(input_dims)) != len(input_dims):
        raise ValueError(f"input_dims has duplicates: {input_dims!r}")
    if set(input_dims) != set(output_dims):
        raise ValueError(
            f"input_dims and output_dims must be the same set of labels (got {input_dims!r} vs {output_dims!r})"
        )
    index_of = {name: index for index, name in enumerate(input_dims)}
    return [index_of[name] for name in output_dims]


def _target_path(
    output_root: Path, output_dims: list[str], indices: list[int], content_dims: int, leaf_ext: str
) -> Path:
    target = output_root
    for index in range(len(output_dims) - content_dims):
        target = target / f"{output_dims[index]}_{indices[index]:04d}"
    if content_dims == 0:
        return target
    if content_dims == 1:
        return target / f"{output_dims[-1]}_{indices[-1]:04d}{leaf_ext}"
    raise NotImplementedError(f"content_dims={content_dims} not supported")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generic N-D arrayed<T> permutation.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--input-dims", required=True)
    parser.add_argument("--output-dims", required=True)
    parser.add_argument("--content-dims", type=int, default=0)
    args = parser.parse_args()
    try:
        input_dims, output_dims = json.loads(args.input_dims), json.loads(args.output_dims)
    except json.JSONDecodeError as exc:
        print(f"ERROR: dim list is not valid JSON: {exc}", file=sys.stderr)
        return 2
    if (
        not isinstance(input_dims, list)
        or not isinstance(output_dims, list)
        or not all(isinstance(x, str) and x for x in input_dims + output_dims)
    ):
        print(
            "ERROR: --input-dims / --output-dims must be JSON lists of non-empty strings",
            file=sys.stderr,
        )
        return 2
    if args.content_dims not in (0, 1):
        print(f"ERROR: --content-dims must be 0 or 1 (got {args.content_dims})", file=sys.stderr)
        return 2
    try:
        perm = _permutation_of(input_dims, output_dims)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    input_root, output_root = Path(args.input), Path(args.output)
    if not input_root.is_dir():
        print(f"ERROR: input is not a directory: {input_root}", file=sys.stderr)
        return 1
    dir_depth = len(input_dims) - args.content_dims
    if dir_depth < 0:
        print("ERROR: content_dims exceeds total dims", file=sys.stderr)
        return 2
    output_root.mkdir(parents=True, exist_ok=True)
    axes = _collect_axis_values(input_root, dir_depth, args.content_dims)
    if any(not values for values in axes):
        print("ERROR: source axis has 0 elements", file=sys.stderr)
        return 1
    positions = [{name: index for index, name in enumerate(values)} for values in axes]
    leaves = _enumerate_source_leaves(input_root, dir_depth, args.content_dims)
    if not leaves:
        print(f"ERROR: no leaves found under {input_root}", file=sys.stderr)
        return 1
    links = 0
    for source_values, leaf in leaves:
        source_indices = [positions[index][name] for index, name in enumerate(source_values)]
        target_indices = [source_indices[index] for index in perm]
        target = _target_path(
            output_root,
            output_dims,
            target_indices,
            int(leaf.is_file()),
            leaf.suffix if leaf.is_file() else "",
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() or target.exists():
            if target.is_symlink() or not target.is_dir():
                target.unlink()
            else:
                continue
        os.symlink(leaf.resolve(), target)
        links += 1
    print(f"regroup (explicit collection axes): {links} leaves, {input_dims!r} → {output_dims!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
