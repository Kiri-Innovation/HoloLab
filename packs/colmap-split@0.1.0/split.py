#!/usr/bin/env python3
"""Split a colmap-cams (cameras.txt + images.txt) into three typed JSON views.

The reverse operation lives in image-undistort's ``recombine_cameras_txt``,
which reads intrinsics.json + distortion.json and rebuilds a byte-equivalent
COLMAP camera line by matching ``camera_id`` and using the model's known
pinhole-prefix / distortion-tail split.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# (pinhole_prefix_names, distortion_tail_names) per COLMAP camera model.
# The prefix len tells us how to split cameras.txt's params array; the
# tail names are only informative — image-undistort concatenates them back
# raw. See src/colmap/sensor/models.h in COLMAP.
_MODEL_LAYOUT: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "SIMPLE_PINHOLE": (("f", "cx", "cy"), ()),
    "PINHOLE": (("fx", "fy", "cx", "cy"), ()),
    "SIMPLE_RADIAL": (("f", "cx", "cy"), ("k",)),
    "RADIAL": (("f", "cx", "cy"), ("k1", "k2")),
    "OPENCV": (("fx", "fy", "cx", "cy"), ("k1", "k2", "p1", "p2")),
    "OPENCV_FISHEYE": (("fx", "fy", "cx", "cy"), ("k1", "k2", "k3", "k4")),
    "FULL_OPENCV": (
        ("fx", "fy", "cx", "cy"),
        ("k1", "k2", "p1", "p2", "k3", "k4", "k5", "k6"),
    ),
    "FOV": (("fx", "fy", "cx", "cy"), ("omega",)),
    "SIMPLE_RADIAL_FISHEYE": (("f", "cx", "cy"), ("k",)),
    "RADIAL_FISHEYE": (("f", "cx", "cy"), ("k1", "k2")),
    "THIN_PRISM_FISHEYE": (
        ("fx", "fy", "cx", "cy"),
        ("k1", "k2", "p1", "p2", "k3", "k4", "sx1", "sy1"),
    ),
}


def _pinhole_to_canonical(pin_names: tuple[str, ...], values: list[float]) -> dict:
    """Return {fx, fy, cx, cy} regardless of whether the model uses one focal or two.

    SIMPLE_* models carry ``[f, cx, cy]`` — we duplicate ``f`` into both
    ``fx`` and ``fy`` so downstream can treat the intrinsic as PINHOLE-ish
    without special-casing. The source ``model`` field lets recombination
    put it back into ``[f, cx, cy]`` if needed.
    """
    named = dict(zip(pin_names, values, strict=True))
    if "f" in named:
        return {"fx": named["f"], "fy": named["f"], "cx": named["cx"], "cy": named["cy"]}
    return {"fx": named["fx"], "fy": named["fy"], "cx": named["cx"], "cy": named["cy"]}


def parse_cameras_txt(path: Path) -> list[dict]:
    """Return one record per camera:

        {camera_id, model, width, height, pinhole_values (list), distortion_values (list)}

    Lines with unknown models are rejected — the model must be one we know
    how to split (i.e. it's in ``_MODEL_LAYOUT``).
    """
    out: list[dict] = []
    for ln in path.read_text().splitlines():
        if not ln or ln.startswith("#"):
            continue
        parts = ln.split()
        camera_id = int(parts[0])
        model = parts[1]
        width = int(parts[2])
        height = int(parts[3])
        raw = [float(x) for x in parts[4:]]
        if model not in _MODEL_LAYOUT:
            raise SystemExit(
                f"unsupported COLMAP camera model {model!r} in {path} — "
                f"colmap-split knows: {sorted(_MODEL_LAYOUT)}"
            )
        pin_names, dist_names = _MODEL_LAYOUT[model]
        want = len(pin_names) + len(dist_names)
        if len(raw) != want:
            raise SystemExit(
                f"{path}:{camera_id}: model {model} expects {want} params, got {len(raw)}"
            )
        out.append(
            {
                "camera_id": camera_id,
                "model": model,
                "width": width,
                "height": height,
                "pinhole_values": raw[: len(pin_names)],
                "distortion_values": raw[len(pin_names) :],
            }
        )
    return out


def parse_images_txt(path: Path) -> list[dict]:
    """Return one record per image: {image_id, camera_id, name, q, t}.

    The alternating 2D-observations line after each pose header is
    intentionally dropped — this is a poses-only view.
    """
    out: list[dict] = []
    lines = path.read_text().splitlines()
    it = iter(lines)
    for ln in it:
        if not ln or ln.startswith("#"):
            continue
        parts = ln.split()
        image_id = int(parts[0])
        q = [float(parts[i]) for i in range(1, 5)]
        t = [float(parts[i]) for i in range(5, 8)]
        camera_id = int(parts[8])
        name = parts[9]
        out.append({"image_id": image_id, "camera_id": camera_id, "name": name, "q": q, "t": t})
        try:
            next(it)  # consume paired 2D-points line
        except StopIteration:
            break
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cams", type=Path, required=True)
    ap.add_argument("--intrinsics-out", type=Path, required=True)
    ap.add_argument("--distortion-out", type=Path, required=True)
    ap.add_argument("--poses-out", type=Path, required=True)
    args = ap.parse_args()

    cams_txt = args.cams / "cameras.txt"
    imgs_txt = args.cams / "images.txt"
    if not cams_txt.is_file() or not imgs_txt.is_file():
        print(f"ERROR: missing cameras.txt / images.txt under {args.cams}", file=sys.stderr)
        return 2

    cams = parse_cameras_txt(cams_txt)
    if not cams:
        print(f"ERROR: no cameras parsed from {cams_txt}", file=sys.stderr)
        return 3
    images = parse_images_txt(imgs_txt)
    if not images:
        print(f"ERROR: no images parsed from {imgs_txt}", file=sys.stderr)
        return 3

    intrinsics_cams = []
    distortion_cams = []
    for c in cams:
        pin_names, _dist_names = _MODEL_LAYOUT[c["model"]]
        canon = _pinhole_to_canonical(pin_names, c["pinhole_values"])
        intrinsics_cams.append(
            {
                "camera_id": c["camera_id"],
                "model": c["model"],
                "width": c["width"],
                "height": c["height"],
                **canon,
            }
        )
        distortion_cams.append(
            {
                "camera_id": c["camera_id"],
                "model": c["model"],
                "params": c["distortion_values"],
            }
        )

    for out_dir in (args.intrinsics_out, args.distortion_out, args.poses_out):
        out_dir.mkdir(parents=True, exist_ok=True)

    (args.intrinsics_out / "intrinsics.json").write_text(
        json.dumps({"schema_version": 1, "cameras": intrinsics_cams}, indent=2) + "\n"
    )
    (args.distortion_out / "distortion.json").write_text(
        json.dumps({"schema_version": 1, "cameras": distortion_cams}, indent=2) + "\n"
    )
    (args.poses_out / "poses.json").write_text(
        json.dumps({"schema_version": 1, "images": images}, indent=2) + "\n"
    )
    print(
        f"colmap-split: {len(intrinsics_cams)} camera(s), {len(images)} image(s) "
        f"→ intrinsics/distortion/poses"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
