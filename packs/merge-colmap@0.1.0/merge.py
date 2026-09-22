#!/usr/bin/env python3
"""Assemble a ``colmap-folder`` from ``colmap-cams`` + ``point-cloud`` (+ optional images).

Mode A (no ``--images``):
    <out>/
      sparse/0/
        cameras.txt   (copied verbatim from --cams)
        images.txt    (copied verbatim from --cams)
        points3D.txt  (copied verbatim from --points)

Mode B (with ``--images`` = arrayed<image>):
    <out>/
      sparse/0/{cameras,images,points3D}.txt   (as above)
      images/
        <NAME>  →  <resolved element file>     (symlink per cam)
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


def _parse_names_from_images_txt(path: Path) -> list[str]:
    """Return the list of NAME fields from a COLMAP ``images.txt``, in file order."""
    names: list[str] = []
    lines = path.read_text().splitlines()
    it = iter(lines)
    for ln in it:
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split()
        if len(parts) < 10:
            continue
        names.append(parts[9])
        try:
            next(it)  # obs line
        except StopIteration:
            break
    return names


def _list_element_files(element_root: Path) -> list[Path]:
    """Return the image files inside one arrayed<image> element dir.

    Accepts the new (flat) layout with files at the element root and
    the legacy ``<element>/frames/<file>`` layout.
    """
    direct = sorted(p for p in element_root.iterdir() if p.is_file() and not p.name.startswith("."))
    if direct:
        return direct
    frames = element_root / "frames"
    if frames.is_dir():
        return sorted(p for p in frames.iterdir() if p.is_file() and not p.name.startswith("."))
    return []


def _cam_key(name: str) -> str:
    """Best-effort mapping ``images.txt`` NAME → element key.

    STG-style NAMEs look like ``../../../c754/frame_sequence/cam00/frame_000000.png``
    → element key ``cam00``. Bare basenames map to themselves-without-extension.
    """
    stem = Path(name).stem
    # For symlink-style paths, prefer the grand-parent dir name.
    parent = Path(name).parent
    if parent.name and parent.name not in (".", ".."):
        return parent.name
    return stem


def _resolve_image_for_name(name: str, images_root: Path) -> Path | None:
    """Locate the file corresponding to a NAME inside an arrayed<image> handle.

    Strategy:
      1. Try direct match on ``images_root / basename(name)`` (flat handle).
      2. Try ``images_root / <element>/<basename>`` for each element dir.
      3. As a fallback, pick the *only* file inside the element dir whose
         key matches ``_cam_key(name)`` — this covers the case where a
         cam element holds one image with a different stem.
    """
    base = Path(name).name

    direct = images_root / base
    if direct.is_file():
        return direct

    element_key = _cam_key(name)
    for element in sorted(images_root.iterdir()):
        if not element.is_dir() or element.name.startswith("."):
            continue
        candidate = element / base
        if candidate.is_file():
            return candidate
        # Fallback: single file inside the element whose parent key matches.
        if element.name == element_key:
            files = _list_element_files(element)
            if len(files) == 1:
                return files[0]
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cams", type=Path, required=True)
    ap.add_argument("--points", type=Path, required=True)
    ap.add_argument("--images", type=Path, required=False)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    src_cams = args.cams / "cameras.txt"
    src_imgs_txt = args.cams / "images.txt"
    src_pts = args.points / "points3D.txt"
    for p in (src_cams, src_imgs_txt, src_pts):
        if not p.is_file():
            print(f"ERROR: missing required file {p}", file=sys.stderr)
            return 2

    sparse0 = args.out / "sparse" / "0"
    sparse0.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src_cams, sparse0 / "cameras.txt")
    shutil.copyfile(src_imgs_txt, sparse0 / "images.txt")
    shutil.copyfile(src_pts, sparse0 / "points3D.txt")

    mode = "A"
    n_imgs = 0
    if args.images is not None:
        if not args.images.is_dir():
            print(f"ERROR: --images root is not a directory: {args.images}", file=sys.stderr)
            return 2
        mode = "B"
        images_out = args.out / "images"
        if images_out.exists():
            shutil.rmtree(images_out)
        images_out.mkdir(parents=True)

        for name in _parse_names_from_images_txt(src_imgs_txt):
            source = _resolve_image_for_name(name, args.images)
            if source is None:
                print(
                    f"ERROR: no image file for NAME {name!r} under {args.images}",
                    file=sys.stderr,
                )
                return 3
            dst = images_out / Path(name).name
            if dst.exists() or dst.is_symlink():
                dst.unlink()
            try:
                os.symlink(source.resolve(), dst)
            except OSError:
                shutil.copy2(source, dst)
            n_imgs += 1

    print(f"[merge-colmap/0.1] done → mode {mode}, images: {n_imgs}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
