#!/usr/bin/env python3
"""Generic N-D permutation over a tag-typed arrayed tree.

Reorders the axes of a multi-dim arrayed handle by a caller-supplied
permutation of dim labels. Handles both physical directory layers AND
the intrinsic content dims of the leaf type T (e.g. ``image_sequence``
= ``<leaf>/frames/frame_XXXXXX.png`` — one intrinsic content dim
enumerated as files under ``frames/``).

Content-dim awareness is opt-in via ``--content-dims N``:

* ``0`` (default) — every dim is a physical dir layer; leaves are dirs.
  Same as the original v0.2.0 shape. Suitable for ``arrayed<arrayed<T>>``
  where T is itself a directory (e.g. ``colmap`` with ``sparse/0/``,
  ``cameras.bin``, ...) and each inner "axis element" is a subdir.
* ``1`` — the innermost dim is enumerated per ``image_sequence`` rules
  (files under ``<leaf>/frames/``), mirroring
  :func:`hololab.gateway.tag_probes.probe_content_dims` for the ``image``
  tag family. Output preserves the T-type layout at each leaf:
  ``<outer_dir>/frames/<inner_label>_<idx:04d><ext>``.

The pack keeps its stdlib-only surface — the ``image_sequence`` rule is
duplicated here rather than imported. Adding a new content-dim tag
family means teaching both places.

Contract
--------

Input: first ``(D - C)`` outer dims are physical subdirs sorted by
:func:`Path.iterdir`; last ``C`` inner dims are enumerated per T-type.
Output: same shape — first ``(D - C)`` are ``<label>_<idx:04d>`` physical
dirs, last ``C`` are the T-type's content layer.

Symlinks — never copies. Idempotent (existing links unlinked first).

Validation
----------

* ``input_dims`` and ``output_dims`` must be the same set of strings.
* ``content_dims`` must satisfy ``0 <= content_dims <= 1`` (only
  ``image_sequence``-style single content dim is supported today).
* Depth of the source tree must equal ``len(input_dims) - content_dims``
  physical layers, terminating in a ``frames/`` dir when ``content_dims=1``.

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
    return sorted(e.name for e in root.iterdir() if e.is_dir() and not e.name.startswith("."))


def _list_content_files_sorted(root: Path) -> list[Path]:
    """image_sequence content probe: files directly under ``<root>/frames/``.

    Mirrors :func:`hololab.gateway.tag_probes._probe_image_sequence_content_dims`
    — the pack keeps this local so ``regroup.py`` stays stdlib-only.
    """
    frames = root / "frames"
    if not frames.is_dir():
        return []
    return sorted(f for f in frames.iterdir() if f.is_file() and not f.name.startswith("."))


def _enumerate_source_leaves(
    root: Path, dir_depth: int, content_dims: int
) -> list[tuple[list[str], Path]]:
    """Walk the input tree; return ``(axis_names, leaf_path)`` per leaf.

    ``axis_names`` is outer-first with length ``dir_depth + content_dims``.
    Physical axes come from directory names; content axes come from the
    T-type's inner enumeration (for ``image_sequence``: the file's name
    including extension — sorted lexicographically).

    ``leaf_path`` is the actual source path — a directory when
    ``content_dims == 0``, a file when the T-type's content layer stores
    files (``image_sequence``).
    """
    out: list[tuple[list[str], Path]] = []

    def walk(node: Path, k: int, prefix: list[str]) -> None:
        if k >= dir_depth:
            if content_dims == 0:
                out.append((prefix.copy(), node))
                return
            if content_dims == 1:
                for f in _list_content_files_sorted(node):
                    out.append(([*prefix, f.name], f))
                return
            raise NotImplementedError(f"content_dims={content_dims} not supported")
        for name in _list_subdirs_sorted(node):
            prefix.append(name)
            walk(node / name, k + 1, prefix)
            prefix.pop()

    walk(root, 0, [])
    return out


def _collect_axis_values(root: Path, dir_depth: int, content_dims: int) -> list[list[str]] | None:
    """Sorted deduped values seen on each axis, outer first.

    Physical axes: subdir names at each level. Content axis (when
    ``content_dims == 1``): unique file names under any leaf ``frames/``,
    sorted lexicographically.
    """
    depth = dir_depth + content_dims
    axes: list[set[str]] = [set() for _ in range(depth)]

    def _walk_phys(node: Path, k: int) -> None:
        if k >= dir_depth:
            if content_dims == 1:
                for f in _list_content_files_sorted(node):
                    axes[dir_depth].add(f.name)
            return
        for name in _list_subdirs_sorted(node):
            axes[k].add(name)
            _walk_phys(node / name, k + 1)

    _walk_phys(root, 0)
    return [sorted(s) for s in axes]


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
    """Compose the target path per output T-type.

    Physical dir layers use ``<label>_<idx:04d>``; the ``image_sequence``
    content layer materializes the innermost element as a file under
    ``<parent>/frames/`` with the same ``<label>_<idx:04d>`` stem and
    the source file's extension.
    """
    dir_depth = len(output_dims) - content_dims
    target = output_root
    for k in range(dir_depth):
        target = target / f"{output_dims[k]}_{tgt_indices[k]:04d}"
    if content_dims == 0:
        return target
    if content_dims == 1:
        label = output_dims[-1]
        idx = tgt_indices[-1]
        return target / "frames" / f"{label}_{idx:04d}{leaf_ext}"
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
        help=(
            "How many innermost dims are enumerated via T-type content rules "
            "(0 = generic dir tree; 1 = image_sequence, files under `frames/`)."
        ),
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
            f"image_sequence-style single content dim is supported today.",
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
        leaf_ext = leaf_path.suffix if content_dims == 1 else ""
        target = _target_path(output_root, output_dims, tgt_indices, content_dims, leaf_ext)
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
    kind = "generic dir-tree" if content_dims == 0 else "image_sequence (frames/*)"
    print(f"regroup ({kind}): {axes_str} = {total_links} leaves, {input_dims!r} → {output_dims!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
