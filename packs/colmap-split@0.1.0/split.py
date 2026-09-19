#!/usr/bin/env python3
"""Split a colmap-cams into two COLMAP-native text handles.

``cameras.txt`` is passed through verbatim — the "camera" in COLMAP's
schema is a monolithic object (model + intrinsics + distortion), and
we honour that. ``images.txt`` is rewritten with the per-image
2D observations stripped: this is a poses-only view. Both outputs
remain drop-in inputs to any COLMAP tool.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def strip_observations(source: Path, dest: Path) -> tuple[int, int]:
    """Rewrite images.txt keeping each pose header line and blanking the paired obs line.

    COLMAP's images.txt is two lines per image (interleaved):
      * ``IMAGE_ID QW QX QY QZ TX TY TZ CAMERA_ID NAME``  (the "header")
      * ``X Y POINT3D_ID X Y POINT3D_ID ...``             (the "obs" — can be empty)

    A real SfM's obs lines are long (hundreds of tokens) — token-count
    heuristics can't reliably tell header from obs, so we lean on the
    strict interleave: every non-comment line consumed as a header eats
    the *next* non-comment line as its obs, no matter its shape.

    Returns ``(headers_written, header_bytes_written_hint)`` for logging.
    """
    lines: list[str] = []
    kept_headers = 0
    it = iter(source.read_text().splitlines())
    for ln in it:
        if not ln or ln.startswith("#"):
            lines.append(ln)
            continue
        # ln is a pose header. Emit it + a blank obs line, then discard
        # whatever the next input line is (that's the source's obs line;
        # may be empty already if the input was a prior split output).
        lines.append(ln)
        lines.append("")
        kept_headers += 1
        try:
            _obs_line = next(it)  # noqa: F841 — intentionally discarded
        except StopIteration:
            break
    dest.write_text("\n".join(lines) + "\n")
    return kept_headers, len(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--cams", type=Path, required=True, help="colmap-cams handle (cameras.txt + images.txt)."
    )
    ap.add_argument(
        "--cameras-out",
        type=Path,
        required=True,
        help="Output handle for colmap-cameras-txt (will contain cameras.txt).",
    )
    ap.add_argument(
        "--poses-out",
        type=Path,
        required=True,
        help="Output handle for colmap-images-txt (will contain images.txt).",
    )
    args = ap.parse_args()

    src_cameras = args.cams / "cameras.txt"
    src_images = args.cams / "images.txt"
    if not src_cameras.is_file() or not src_images.is_file():
        print(
            f"ERROR: input cams handle missing cameras.txt / images.txt under {args.cams}",
            file=sys.stderr,
        )
        return 2

    args.cameras_out.mkdir(parents=True, exist_ok=True)
    args.poses_out.mkdir(parents=True, exist_ok=True)

    # cameras.txt is passed through verbatim — that IS the split camera view.
    shutil.copyfile(src_cameras, args.cameras_out / "cameras.txt")
    kept, _n = strip_observations(src_images, args.poses_out / "images.txt")

    print(f"colmap-split: copied cameras.txt; wrote images.txt with {kept} pose header(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
