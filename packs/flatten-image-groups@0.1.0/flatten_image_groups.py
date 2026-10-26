#!/usr/bin/env python3
"""Flatten a two-axis image tree into root-level, path-encoded image files."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

SEPARATOR = "__"


def _visible(path: Path) -> list[Path]:
    return sorted(child for child in path.iterdir() if not child.name.startswith("."))


def flatten(input_root: Path, output_root: Path) -> dict:
    if not input_root.is_dir():
        raise ValueError(f"input is not a directory: {input_root}")
    groups = _visible(input_root)
    if not groups:
        raise ValueError(f"{input_root}: no group elements")
    output_root.mkdir(parents=True, exist_ok=True)
    records = []
    methods = {"hardlink": 0, "copy": 0}
    seen: set[str] = set()

    for group in groups:
        if not group.is_dir():
            raise ValueError(f"{group}: expected outer group directory")
        if SEPARATOR in group.name:
            raise ValueError(f"{group}: reserved separator {SEPARATOR!r} in group name")
        leaves = _visible(group)
        if not leaves:
            raise ValueError(f"{group}: empty image group")
        for source in leaves:
            if not source.is_file():
                raise ValueError(f"{source}: expected image file at second axis")
            if SEPARATOR in source.name:
                raise ValueError(f"{source}: reserved separator {SEPARATOR!r} in image name")
            flat_name = f"{group.name}{SEPARATOR}{source.name}"
            if flat_name in seen:
                raise ValueError(f"duplicate flattened element {flat_name!r}")
            seen.add(flat_name)
            target = output_root / flat_name
            try:
                os.link(source.resolve(), target)
                method = "hardlink"
            except OSError:
                shutil.copy2(source.resolve(), target)
                method = "copy"
            methods[method] += 1
            records.append(
                {
                    "element": flat_name,
                    "group": group.name,
                    "image": source.name,
                    "source": source.relative_to(input_root).as_posix(),
                    "materialization": method,
                }
            )

    metadata = output_root / ".flatten-image-groups"
    metadata.mkdir(exist_ok=True)
    manifest = {
        "format": "hololab.flatten-image-groups",
        "format_version": 1,
        "separator": SEPARATOR,
        "source_axes": ["group", "cam"],
        "output_axes": [],
        "groups": len(groups),
        "images": len(records),
        "materialization": methods,
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
        f"flatten-image-groups: {result['groups']} groups / "
        f"{result['images']} images -> one dimension",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
