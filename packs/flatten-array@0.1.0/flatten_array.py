#!/usr/bin/env python3
"""Flatten an opaque two-axis array tree into a one-axis array view."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

SEPARATOR = "__"


def _visible(path: Path) -> list[Path]:
    return sorted(child for child in path.iterdir() if not child.name.startswith("."))


def flatten(input_root: Path, output_root: Path) -> dict:
    if not input_root.is_dir():
        raise ValueError(f"input is not a directory: {input_root}")
    outers = _visible(input_root)
    if not outers:
        raise ValueError(f"{input_root}: no outer elements")
    output_root.mkdir(parents=True, exist_ok=True)
    records = []
    seen: set[str] = set()

    for outer in outers:
        if not outer.is_dir():
            raise ValueError(f"{outer}: expected outer array directory")
        if SEPARATOR in outer.name:
            raise ValueError(f"{outer}: reserved separator {SEPARATOR!r} in outer name")
        inners = _visible(outer)
        if not inners:
            raise ValueError(f"{outer}: empty inner array")
        for source in inners:
            if SEPARATOR in source.name:
                raise ValueError(f"{source}: reserved separator {SEPARATOR!r} in inner name")
            flat_name = f"{outer.name}{SEPARATOR}{source.name}"
            if flat_name in seen:
                raise ValueError(f"duplicate flattened element {flat_name!r}")
            seen.add(flat_name)
            target = output_root / flat_name
            if target.exists() or target.is_symlink():
                raise FileExistsError(f"output element already exists: {target}")
            os.symlink(source.resolve(), target, target_is_directory=source.is_dir())
            records.append(
                {
                    "element": flat_name,
                    "outer": outer.name,
                    "inner": source.name,
                    "source": source.relative_to(input_root).as_posix(),
                    "leaf_kind": "directory" if source.is_dir() else "file",
                    "materialization": "symlink",
                }
            )

    metadata = output_root / ".flatten-array"
    metadata.mkdir(exist_ok=True)
    manifest = {
        "format": "hololab.flatten-array",
        "format_version": 1,
        "separator": SEPARATOR,
        "input_rank": 2,
        "output_rank": 1,
        "outer_elements": len(outers),
        "output_elements": len(records),
        "members": records,
    }
    (metadata / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output_root / ".hololab-done").write_text("ok\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = flatten(args.input.resolve(), args.output.resolve())
    print(
        f"flatten-array: 2-D ({result['outer_elements']} outer) -> "
        f"1-D ({result['output_elements']} leaves)",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
