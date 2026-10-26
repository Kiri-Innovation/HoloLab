#!/usr/bin/env python3
"""Standard-library implementation of builtin/flatten-array."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

SEPARATOR = "__"


def flatten(input_root: Path, output_root: Path) -> dict:
    if not input_root.is_dir():
        raise ValueError(f"input is not a directory: {input_root}")
    outers = sorted(item for item in input_root.iterdir() if not item.name.startswith("."))
    if not outers:
        raise ValueError(f"{input_root}: no outer elements")
    output_root.mkdir(parents=True, exist_ok=True)
    records = []
    for outer in outers:
        if not outer.is_dir() or SEPARATOR in outer.name:
            raise ValueError(f"invalid outer array element: {outer}")
        inners = sorted(item for item in outer.iterdir() if not item.name.startswith("."))
        if not inners:
            raise ValueError(f"{outer}: empty inner array")
        for source in inners:
            if SEPARATOR in source.name:
                raise ValueError(f"reserved separator in {source}")
            target = output_root / f"{outer.name}{SEPARATOR}{source.name}"
            if target.exists() or target.is_symlink():
                raise FileExistsError(f"output element already exists: {target}")
            os.symlink(source.resolve(), target, target_is_directory=source.is_dir())
            records.append(
                {
                    "element": target.name,
                    "outer": outer.name,
                    "inner": source.name,
                    "source": source.relative_to(input_root).as_posix(),
                    "leaf_kind": "directory" if source.is_dir() else "file",
                    "materialization": "symlink",
                }
            )
    metadata = output_root / ".flatten-array"
    metadata.mkdir(exist_ok=True)
    (metadata / "manifest.json").write_text(
        json.dumps(
            {
                "format": "hololab.flatten-array",
                "format_version": 1,
                "separator": SEPARATOR,
                "input_rank": 2,
                "output_rank": 1,
                "outer_elements": len(outers),
                "output_elements": len(records),
                "members": records,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (output_root / ".hololab-done").write_text("ok\n", encoding="utf-8")
    return {
        "outer_elements": len(outers),
        "output_elements": len(records),
        "members": records,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = flatten(args.input.resolve(), args.output.resolve())
    print(
        f"flatten-array: 2-D ({result['outer_elements']} outer) -> 1-D ({result['output_elements']} leaves)"
    )
