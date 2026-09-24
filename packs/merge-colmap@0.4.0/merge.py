#!/usr/bin/env python3
"""Assemble a ``colmap-folder`` from ``colmap-cams`` + ``point-cloud`` (+ optional images).

Mode A (no ``--images``):
    <out>/
      sparse/0/
        cameras.txt   (copied verbatim from --cams)
        images.txt    (copied verbatim from --cams)
        points3D.txt  (copied verbatim from --points)

Mode B (with ``--images``):
    <out>/
      sparse/0/{cameras,images,points3D}.txt   (as above)
      images/
        <NAME_basename>  ->  <resolved element file>   (symlink per cam)

Under the arrayable @0.2.0 wire, ``--images`` is one frame's element
directory (files inline). Under the legacy @0.1.0 wire, ``--images`` is
the aggregate ``arrayed<image>`` root (many element subdirs). This
script handles both by first checking for files directly under the
handle root; if none are found it walks one subdir layer down.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Vendored trailing-integer resolver — SfM's ``cam00`` matches
# regroup@0.2.0's ``cam_0000.png`` via numeric_suffix. Same module
# colmap-triangulate@0.7.0 uses; kept vendored per pack so both packs
# are self-contained.
_PACK_DIR = Path(__file__).resolve().parent
if str(_PACK_DIR) not in sys.path:
    sys.path.insert(0, str(_PACK_DIR))

from sfm_key import (  # noqa: E402
    cam_key,
    numeric_suffix,
)


def _parse_pose_rows(path: Path) -> tuple[list[str], list[tuple[int, list[str]]]]:
    """Return (header_lines, [(line_no, tokenised_pose_row), …]) for images.txt.

    ``tokenised_pose_row`` retains the exact whitespace-split tokens so we
    can rewrite the last column (NAME) in-place without disturbing the
    pose numbers. Observation lines are dropped from the returned list
    but their original text is preserved via the raw ``lines`` output —
    the caller reassembles by writing header + rewritten pose rows +
    a blank observation line pair. Same layout downstream tools expect.
    """
    raw = path.read_text().splitlines()
    header: list[str] = []
    poses: list[tuple[int, list[str]]] = []
    it = enumerate(raw)
    for i, ln in it:
        s = ln.strip()
        if not s:
            header.append(ln)
            continue
        if s.startswith("#"):
            header.append(ln)
            continue
        parts = s.split()
        if len(parts) < 10:
            continue
        poses.append((i, parts))
        try:
            next(it)  # obs line
        except StopIteration:
            break
    return header, poses


def _parse_names_from_images_txt(path: Path) -> list[str]:
    """Return the list of NAME fields from a COLMAP ``images.txt``, in file order."""
    _hdr, poses = _parse_pose_rows(path)
    return [row[9] for _line, row in poses]


def _list_element_files(element_root: Path) -> list[Path]:
    """Image files inside one arrayed<image> element dir (flat + legacy frames/)."""
    direct = sorted(p for p in element_root.iterdir() if p.is_file() and not p.name.startswith("."))
    if direct:
        return direct
    frames = element_root / "frames"
    if frames.is_dir():
        return sorted(p for p in frames.iterdir() if p.is_file() and not p.name.startswith("."))
    return []


def _staged_files(images_root: Path) -> list[Path]:
    """Return the pool of candidate image files from the ``--images`` handle.

    Two shapes accepted:

    * Flat per-shard (arrayable @0.2.0): files sit directly under
      ``images_root`` (``images_root/cam_0000.png``).
    * Aggregate arrayed<image> (legacy @0.1.0): files live inside
      element subdirs (``images_root/<element>/<basename>``). Every
      element's files are pooled — sufficient for a single-frame merge
      because NAMEs still resolve unambiguously by cam key.
    """
    direct = sorted(p for p in images_root.iterdir() if p.is_file() and not p.name.startswith("."))
    if direct:
        return direct
    pool: list[Path] = []
    for element in sorted(images_root.iterdir()):
        if not element.is_dir() or element.name.startswith("."):
            continue
        pool.extend(_list_element_files(element))
    return pool


def _build_cam_file_map(
    files: list[Path],
) -> tuple[dict[str, Path], dict[int, Path] | None]:
    """Return (cam_key -> file, numeric_suffix -> file OR None).

    The numeric index is only usable when trailing integers are unique
    across the file set. Ambiguous file names (e.g. ``cam01.png`` +
    ``cam_0001.png``) disable the fallback rather than silently pick
    one.
    """
    by_key: dict[str, Path] = {}
    for f in files:
        by_key[cam_key(f.name)] = f
    by_num: dict[int, Path] | None = {}
    for key, f in by_key.items():
        n = numeric_suffix(key)
        if n is None or n in by_num:
            by_num = None
            break
        by_num[n] = f
    return by_key, by_num


def _resolve_image_for_name(
    name: str,
    files_by_key: dict[str, Path],
    files_by_num: dict[int, Path] | None,
) -> Path | None:
    """Locate the file corresponding to a COLMAP ``NAME`` in the staged pool.

    Direct cam-key match first. On miss, fall back to trailing-integer
    match (regroup@0.2.0's ``cam_0000.png`` bridges to SfM's ``cam00``).
    Mirrors the resolver inside ``colmap-triangulate@0.7.0``.
    """
    sfm_key = cam_key(name)
    if sfm_key in files_by_key:
        return files_by_key[sfm_key]
    if files_by_num is None:
        return None
    n = numeric_suffix(sfm_key)
    if n is None:
        return None
    return files_by_num.get(n)


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
    shutil.copyfile(src_pts, sparse0 / "points3D.txt")

    mode = "A"
    n_imgs = 0
    if args.images is None:
        # Mode A: publish images.txt verbatim.
        shutil.copyfile(src_imgs_txt, sparse0 / "images.txt")
    else:
        if not args.images.is_dir():
            print(f"ERROR: --images root is not a directory: {args.images}", file=sys.stderr)
            return 2
        mode = "B"
        images_out = args.out / "images"
        if images_out.exists():
            shutil.rmtree(images_out)
        images_out.mkdir(parents=True)

        header, poses = _parse_pose_rows(src_imgs_txt)
        files = _staged_files(args.images)
        if not files:
            print(f"ERROR: no image files found under {args.images}", file=sys.stderr)
            return 3
        files_by_key, files_by_num = _build_cam_file_map(files)

        # Rewrite each pose's NAME to a flat basename that matches the
        # actual file we place under images/. SfM NAMEs point at the
        # original fx path (``../../../<uuid>/frame_sequence/cam00/frame_XXXXXX.png``)
        # which collides on basename when the SfM was solved over one
        # frame across 21 cams — 21 poses referencing the same
        # ``frame_000000.png``. Downstream tools (STG's dataset_readers,
        # colmap model_converter --output_type BIN) load images by
        # NAME + image_path root, so we need a 1:1 NAME↔file mapping.
        rewritten_lines: list[str] = list(header)
        rewrites: list[tuple[str, str, Path]] = []  # (orig NAME, new NAME, source)
        used_names: set[str] = set()
        for _lineno, row in poses:
            orig_name = row[9]
            source = _resolve_image_for_name(orig_name, files_by_key, files_by_num)
            if source is None:
                fallback_hint = (
                    ""
                    if files_by_num is not None
                    else " (numeric fallback disabled - ambiguous file names)"
                )
                sample_keys = ", ".join(sorted(files_by_key)[:6])
                print(
                    f"ERROR: no image file for NAME {orig_name!r} "
                    f"(cam_key={cam_key(orig_name)!r}) under {args.images}. "
                    f"Available cam keys: [{sample_keys}]{fallback_hint}",
                    file=sys.stderr,
                )
                return 3
            # Prefer the SfM cam_key as the new basename so downstream tools
            # can still read a cam identifier from images.txt. Extension
            # follows the source file.
            new_name = f"{cam_key(orig_name)}{source.suffix}"
            # Guard against collision (two SfM poses with the same cam_key
            # would silently overwrite the same file); fall back to the
            # source file's own basename in that case.
            if new_name in used_names:
                new_name = source.name
                if new_name in used_names:
                    # Last resort: index-suffix.
                    new_name = f"{Path(new_name).stem}_{len(used_names)}{source.suffix}"
            used_names.add(new_name)

            new_row = list(row)
            new_row[9] = new_name
            rewritten_lines.append(" ".join(new_row))
            rewritten_lines.append("")  # blank observation line
            rewrites.append((orig_name, new_name, source))

        (sparse0 / "images.txt").write_text("\n".join(rewritten_lines) + "\n")

        for _orig, new_name, source in rewrites:
            dst = images_out / new_name
            if dst.exists() or dst.is_symlink():
                dst.unlink()
            try:
                os.symlink(source.resolve(), dst)
            except OSError:
                shutil.copy2(source, dst)
            n_imgs += 1

    # STG's Technicolor loader (scene/dataset_readers.py:1054-1063) unconditionally
    # reads sparse/0/points3D.bin via read_points3D_binary — no .txt fallback.
    # Emit BIN siblings so downstream stg-train@0.3.0 can consume our output.
    subprocess.run(
        [
            "colmap",
            "model_converter",
            "--input_path",
            str(sparse0),
            "--output_path",
            str(sparse0),
            "--output_type",
            "BIN",
        ],
        check=True,
    )

    print(f"[merge-colmap/0.2] done -> mode {mode}, images: {n_imgs}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
