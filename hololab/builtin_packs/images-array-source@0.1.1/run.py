#!/usr/bin/env python3
"""Expose an existing directory of frames as an image handle, decoding HEIC to real PNG files."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".heic"}


def read_orientation(exif_bytes):
    """Read before to_pillow(), which normalizes the tag without rotating pixels."""
    from PIL import Image

    exif = Image.Exif()
    try:
        if exif_bytes:
            exif.load(exif_bytes)
        orientation = exif.get(0x0112, 1)
    except (OSError, ValueError, TypeError, SyntaxError):
        print("invalid HEIC EXIF; leaving pixel orientation unchanged", file=sys.stderr)
        return 1
    if not isinstance(orientation, int) or orientation not in range(1, 9):
        print("invalid HEIC EXIF orientation; leaving pixels unchanged", file=sys.stderr)
        return 1
    return orientation


def orient_pixels(decoded, orientation):
    """Bake all eight EXIF orientations into pixels, including the alpha channel."""
    from PIL import Image

    operations = {
        2: Image.Transpose.FLIP_LEFT_RIGHT,
        3: Image.Transpose.ROTATE_180,
        4: Image.Transpose.FLIP_TOP_BOTTOM,
        5: Image.Transpose.TRANSPOSE,
        6: Image.Transpose.ROTATE_270,
        7: Image.Transpose.TRANSVERSE,
        8: Image.Transpose.ROTATE_90,
    }
    operation = operations.get(orientation)
    upright = decoded.transpose(operation) if operation is not None else decoded.copy()
    exif = upright.getexif()
    exif.pop(0x0112, None)
    if exif:
        upright.info["exif"] = exif.tobytes()
    else:
        upright.info.pop("exif", None)
    return upright


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames-path", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    source: Path = args.frames_path
    if not source.is_dir():
        print(f"frames_path not found or not a directory: {source}", file=sys.stderr)
        return 2

    frames = sorted(
        (
            path
            for path in source.iterdir()
            if not path.name.startswith(".")
            and (path.is_file() or path.is_symlink())
            and path.suffix.lower() in IMAGE_EXTENSIONS
        ),
        key=lambda path: os.fsencode(path.name),
    )
    if not frames:
        print(
            f"no image files ({', '.join(sorted(IMAGE_EXTENSIONS))}) found in {source}",
            file=sys.stderr,
        )
        return 2

    output: Path = args.output
    if output.resolve() == source.resolve() or source.resolve().is_relative_to(output.resolve()):
        print("output must not be the source directory or its ancestor", file=sys.stderr)
        return 2

    names = [
        frame.with_suffix(".png").name if frame.suffix.lower() == ".heic" else frame.name
        for frame in frames
    ]
    # Keep byte-sorted directory enumeration in exactly the source sequence.
    # Retain the HEIC extension as part of the stem if replacing it collides
    # with a JPEG/PNG or changes that ordering (e.g. a.heic + a.jpg).
    if len(set(names)) != len(names) or names != sorted(names, key=os.fsencode):
        names = [
            frame.name + ".png" if frame.suffix.lower() == ".heic" else frame.name
            for frame in frames
        ]
    if len(set(names)) != len(names) or names != sorted(names, key=os.fsencode):
        print(
            "output names collide or change sequence order; rename source copies first",
            file=sys.stderr,
        )
        return 2

    output.mkdir(parents=True, exist_ok=True)
    converted = 0
    for index, (frame, name) in enumerate(zip(frames, names, strict=True), 1):
        target = output / name
        try:
            if frame.suffix.lower() == ".heic":
                # Lazy import keeps the non-HEIC path dependency-free.
                import pillow_heif

                heif = pillow_heif.open_heif(frame, convert_hdr_to_8bit=False)
                if heif.info.get("bit_depth", 8) > 8:
                    raise ValueError("HDR/high-bit-depth HEIC is unsupported without pixel loss")
                orientation = read_orientation(heif.info.get("exif"))
                with heif.to_pillow() as decoded, orient_pixels(decoded, orientation) as upright:
                    if target.is_symlink() or target.exists():
                        target.unlink()
                    upright.save(target, format="PNG", exif=upright.info.get("exif", b""))
                converted += 1
            else:
                if target.is_symlink() or target.exists():
                    target.unlink()
                target.symlink_to(frame.resolve())
        except (ImportError, OSError, ValueError) as exc:
            print(
                f"failed to process {frame}: {exc}; HEIC requires Pillow and pillow-heif "
                "in the host Python environment",
                file=sys.stderr,
            )
            return 2
        print(f"[ITER {index}/{len(frames)}] {frame.name} -> {name}", flush=True)

    print(
        f"converted {converted} HEIC image(s) to PNG; "
        f"linked {len(frames) - converted} frame(s) from {source} into {output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
