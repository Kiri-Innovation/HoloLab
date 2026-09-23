#!/usr/bin/env python3
"""Per-frame image undistort — colmap-cams (OPENCV) + image -> colmap-cams (PINHOLE) + image.

Bundle-in, bundle-out. Consumes the SfM's ``colmap-cams`` directly (no
intermediate tri output required) — see the manifest ``docs`` for the
empirical basis. Each invocation is one shard = one frame. The output
``colmap-cams`` bundle is a full COLMAP text sparse model (cameras.txt +
images.txt + empty points3D.txt) at PINHOLE resolution.

The heavy lifting is a single ``colmap image_undistorter`` call in
COLMAP-output mode; this script:

1. Stages the shard's images into a flat scratch dir (avoids COLMAP's
   symlink-resolving image_path walker pushing outputs out of the deliverable).
2. Copies the SfM sparse model into scratch and rewrites images.txt NAMEs
   to match the shard's actual file basenames (via the vendored
   ``sfm_key`` cam-key + trailing-integer bridge — same one
   ``colmap-triangulate@0.7.0`` and ``merge-colmap@0.2.0`` use).
3. Runs ``image_undistorter --output_type COLMAP``.
4. Republishes the emitted PINHOLE ``cameras.txt`` + rewritten
   ``images.txt`` on the ``cams`` output and the undistorted image
   files on the ``images`` output.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

_PACK_DIR = Path(__file__).resolve().parent
if str(_PACK_DIR) not in sys.path:
    sys.path.insert(0, str(_PACK_DIR))

from sfm_key import (  # noqa: E402
    cam_key,
    numeric_suffix,
)


def stage_images(source_dir: Path, scratch_input: Path) -> list[Path]:
    """Hardlink shard image files into ``scratch_input``; return the staged paths."""
    if scratch_input.exists():
        shutil.rmtree(scratch_input)
    scratch_input.mkdir(parents=True)
    if any(p.is_file() and not p.name.startswith(".") for p in source_dir.iterdir()):
        walk_root = source_dir
    else:
        # Legacy ``frames/`` wrapper (pre-flatten handles).
        walk_root = source_dir / "frames"
        if not walk_root.is_dir():
            raise SystemExit(f"no image files at {source_dir} or its frames/ subdir")

    staged: list[Path] = []
    for src in sorted(walk_root.iterdir()):
        if not (src.is_file() or src.is_symlink()):
            continue
        real = src.resolve()
        if not real.exists():
            raise SystemExit(f"broken image link: {src} -> {real}")
        dst = scratch_input / src.name
        try:
            os.link(real, dst)
        except OSError:
            shutil.copy2(real, dst)
        staged.append(dst)
    if not staged:
        raise SystemExit(f"no images staged from {source_dir}")
    return staged


def _parse_pose_rows(path: Path) -> tuple[list[str], list[list[str]]]:
    """Return (header_lines, tokenised_pose_rows) for a COLMAP images.txt."""
    raw = path.read_text().splitlines()
    header: list[str] = []
    poses: list[list[str]] = []
    it = enumerate(raw)
    for _i, ln in it:
        s = ln.strip()
        if not s or s.startswith("#"):
            header.append(ln)
            continue
        parts = s.split()
        if len(parts) < 10:
            continue
        poses.append(parts)
        try:
            next(it)  # obs line
        except StopIteration:
            break
    return header, poses


def _build_cam_file_map(files: list[Path]) -> tuple[dict[str, Path], dict[int, Path] | None]:
    """Return (cam_key -> file, numeric_suffix -> file OR None)."""
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


def _resolve_file_for_sfm_name(
    name: str,
    by_key: dict[str, Path],
    by_num: dict[int, Path] | None,
) -> Path | None:
    """Look up the staged file matching a SfM images.txt NAME entry."""
    sk = cam_key(name)
    if sk in by_key:
        return by_key[sk]
    if by_num is None:
        return None
    n = numeric_suffix(sk)
    if n is None:
        return None
    return by_num.get(n)


def stage_sparse_prior(
    sfm_cams_dir: Path,
    scratch_prior: Path,
    staged_files: list[Path],
) -> list[tuple[str, str]]:
    """Copy the SfM sparse model into scratch and rewrite images.txt NAMEs.

    NAMEs are rewritten to the actual staged file basenames so
    ``image_undistorter --image_path`` finds every image. Returns the
    list of ``(orig_name, new_name)`` pairs for logging.
    """
    if scratch_prior.exists():
        shutil.rmtree(scratch_prior)
    scratch_prior.mkdir(parents=True)

    src_cams = sfm_cams_dir / "cameras.txt"
    src_imgs = sfm_cams_dir / "images.txt"
    if not src_cams.is_file() or not src_imgs.is_file():
        raise SystemExit(f"SfM cams dir missing cameras.txt / images.txt: {sfm_cams_dir}")
    shutil.copyfile(src_cams, scratch_prior / "cameras.txt")

    header, poses = _parse_pose_rows(src_imgs)
    by_key, by_num = _build_cam_file_map(staged_files)

    rewrites: list[tuple[str, str]] = []
    used: set[str] = set()
    lines: list[str] = list(header)
    for row in poses:
        orig = row[9]
        f = _resolve_file_for_sfm_name(orig, by_key, by_num)
        if f is None:
            keys = ", ".join(sorted(by_key)[:6])
            raise SystemExit(
                f"no staged file matches SfM NAME {orig!r} (cam_key={cam_key(orig)!r}); "
                f"staged cam keys: [{keys}]"
            )
        new_name = f.name
        if new_name in used:
            # Two SfM rows collided onto one file; refuse to guess.
            raise SystemExit(
                f"NAME rewrite collision: {orig!r} and an earlier row both point to "
                f"{new_name!r} in the staged set — cannot proceed"
            )
        used.add(new_name)
        row[9] = new_name
        lines.append(" ".join(row))
        lines.append("")  # blank obs line
        rewrites.append((orig, new_name))
    (scratch_prior / "images.txt").write_text("\n".join(lines) + "\n")
    (scratch_prior / "points3D.txt").write_text("")
    return rewrites


def run(cmd: list, step: str) -> None:
    print(f"[image-undistort/0.4] {step}", flush=True)
    subprocess.run([str(x) for x in cmd], check=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cams", type=Path, required=True, help="SfM colmap-cams handle (OPENCV).")
    ap.add_argument(
        "--images",
        type=Path,
        required=True,
        help="Per-frame image directory (files inline at the handle root).",
    )
    ap.add_argument(
        "--cams-out",
        type=Path,
        required=True,
        help="Output colmap-cams handle (PINHOLE cameras.txt + rewritten images.txt).",
    )
    ap.add_argument(
        "--images-out",
        type=Path,
        required=True,
        help="Output image handle (undistorted image files at handle root).",
    )
    ap.add_argument("--scratch", type=Path, required=True)
    ap.add_argument("--blank-pixels", type=int, default=0)
    args = ap.parse_args()

    if not args.images.is_dir():
        print(f"ERROR: images handle is not a directory: {args.images}", file=sys.stderr)
        return 2

    args.cams_out.mkdir(parents=True, exist_ok=True)
    args.images_out.mkdir(parents=True, exist_ok=True)
    args.scratch.mkdir(parents=True, exist_ok=True)

    scratch_input = args.scratch / "input"
    scratch_prior = args.scratch / "prior"
    scratch_output = args.scratch / "undistorter_out"
    if scratch_output.exists():
        shutil.rmtree(scratch_output)
    scratch_output.mkdir(parents=True)

    staged = stage_images(args.images, scratch_input)
    rewrites = stage_sparse_prior(args.cams, scratch_prior, staged)

    run(
        [
            "colmap",
            "image_undistorter",
            "--image_path",
            scratch_input,
            "--input_path",
            scratch_prior,
            "--output_path",
            scratch_output,
            "--output_type",
            "COLMAP",
            "--blank_pixels",
            args.blank_pixels,
        ],
        f"image_undistorter (N={len(staged)}, blank_pixels={args.blank_pixels})",
    )

    sparse_root = scratch_output / "sparse"
    if not sparse_root.is_dir():
        print(f"ERROR: image_undistorter produced no sparse/: {sparse_root}", file=sys.stderr)
        return 3
    sparse_0 = sparse_root / "0"
    sparse_0.mkdir(exist_ok=True)
    for name in ("cameras.bin", "images.bin", "points3D.bin"):
        src = sparse_root / name
        if src.exists():
            shutil.move(str(src), str(sparse_0 / name))
    run(
        [
            "colmap",
            "model_converter",
            "--input_path",
            sparse_0,
            "--output_path",
            sparse_0,
            "--output_type",
            "TXT",
        ],
        "model_converter -> TXT",
    )

    # Publish the PINHOLE bundle (cameras.txt + images.txt) as colmap-cams.
    for basename in ("cameras.txt", "images.txt"):
        src = sparse_0 / basename
        if not src.is_file():
            print(f"ERROR: image_undistorter produced no {basename}: {src}", file=sys.stderr)
            return 3
        shutil.copyfile(src, args.cams_out / basename)

    # Publish the undistorted image files (flat under the handle root).
    out_root = args.images_out
    for stale in out_root.glob("*"):
        if stale.is_file() and not stale.name.startswith("."):
            stale.unlink()
    src_images = scratch_output / "images"
    n_out = 0
    for src in sorted(src_images.iterdir()):
        if not src.is_file():
            continue
        dst = out_root / src.name
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)
        n_out += 1
    if n_out != len(staged):
        print(
            f"ERROR: undistorter produced {n_out} images, expected {len(staged)}",
            file=sys.stderr,
        )
        return 3

    fallback_hits = sum(1 for orig, new in rewrites if cam_key(orig) != Path(new).stem)
    print(
        f"[image-undistort/0.4] done -> PINHOLE cams + {n_out} undistorted images "
        f"(NAME rewrites: {len(rewrites)}; via numeric bridge: {fallback_hits})",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
