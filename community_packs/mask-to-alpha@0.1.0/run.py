#!/usr/bin/env python3
"""Flat image arrays -> RGBA PNGs, paired exclusively by array position."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import numpy as np
from PIL import Image


def image_group(root: Path) -> list[Path]:
    entries = sorted(
        (p for p in root.iterdir() if not p.name.startswith(".")), key=lambda p: os.fsencode(p.name)
    )
    if not entries or any(not p.is_file() for p in entries):
        raise ValueError(f"Expected a nonempty flat image array: {root}")
    return entries


def apply_masks(images: Path, masks: Path, output: Path, verify_source_hash=True) -> int:
    sources, mattes = image_group(images), image_group(masks)
    if len(sources) != len(mattes):
        raise ValueError(f"Image/mask count mismatch: {len(sources)} != {len(mattes)}")
    records = None
    if verify_source_hash:
        metadata = masks / ".validation.json"
        if not metadata.is_file():
            raise ValueError(f"Missing source RGB hash metadata: {metadata}")
        records = json.loads(metadata.read_text())["frames"]
        if len(records) != len(mattes):
            raise ValueError("Mask metadata count mismatch")
        for i, record in enumerate(records):
            digest = record.get("rgb_sha256", "")
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError(f"Invalid rgb_sha256 at index {i}")
    # Fail dimension/count checks before writing anything. Names never pair inputs.
    for i, (src, matte) in enumerate(zip(sources, mattes, strict=True)):
        with Image.open(src) as im, Image.open(matte) as mask:
            if im.size != mask.size:
                raise ValueError(f"Size mismatch at index {i}: {im.size} != {mask.size}")
            if mask.mode not in ("1", "L", "RGB", "RGBA", "P"):
                raise ValueError(
                    f"Expected an 8-bit grayscale-compatible mask at index {i}: {mask.mode}"
                )
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError(f"Output must be empty: {output}")
    report = []
    for i, (src, matte) in enumerate(zip(sources, mattes, strict=True)):
        with Image.open(src) as im, Image.open(matte) as mask:
            rgb = im.convert("RGB")
            digest = hashlib.sha256(np.asarray(rgb).tobytes()).hexdigest()
            if records is not None and digest != records[i]["rgb_sha256"]:
                raise ValueError(f"Source RGB SHA256 mismatch at index {i}: {src}, {matte}")
            rgba = rgb.convert("RGBA")
            rgba.putalpha(mask.convert("L"))
            name = f"p_{i:016d}.png"
            rgba.save(output / name, compress_level=1)
        report.append(
            {"name": name, "rgb_sha256": digest, "source_hash_verified": records is not None}
        )
        print(f"[ITER {i + 1}/{len(sources)}]", flush=True)
    (output / ".validation.json").write_text(
        json.dumps({"count": len(report), "frames": report}, indent=2) + "\n"
    )
    (output / ".hololab-done").touch()
    return len(sources)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("images", "masks", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument(
        "--verify-source-hash", type=str.lower, choices=("true", "false"), default="true"
    )
    args = parser.parse_args()
    apply_masks(args.images, args.masks, args.output, args.verify_source_hash == "true")


if __name__ == "__main__":
    main()
