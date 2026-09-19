#!/usr/bin/env python3
"""Undistort one (intrinsics, distortion, images) triple via COLMAP's image_undistorter.

Pack contract is scalar-per-invocation — batch is the framework's job via
``arrayed_toggle`` fan-out. See manifest.yaml docs for the framework-level
zip semantics + length-mismatch behavior.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Same table colmap-split uses. Duplicated intentionally: packs are meant
# to be self-contained per pack-spec, no cross-pack Python imports.
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


def _fmt(v: float) -> str:
    """Same 17g formatting COLMAP itself round-trips through."""
    return f"{v:.17g}"


def recombine_cameras_txt(intr: dict, dist: dict) -> str:
    """intrinsics.json + distortion.json → COLMAP cameras.txt body (no header).

    Match by ``camera_id``; verify model equality; write the pinhole prefix
    (3 or 4 numbers depending on ``SIMPLE_*``-ness of the model) followed
    by the distortion tail params, in the exact order COLMAP expects.
    """
    intr_by_id = {c["camera_id"]: c for c in intr["cameras"]}
    dist_by_id = {c["camera_id"]: c for c in dist["cameras"]}
    only_intr = intr_by_id.keys() - dist_by_id.keys()
    only_dist = dist_by_id.keys() - intr_by_id.keys()
    if only_intr or only_dist:
        raise SystemExit(
            f"intrinsics and distortion disagree on camera_id set — "
            f"only-in-intrinsics={sorted(only_intr)}, "
            f"only-in-distortion={sorted(only_dist)}"
        )

    lines: list[str] = []
    for cid in sorted(intr_by_id):
        i = intr_by_id[cid]
        d = dist_by_id[cid]
        if i["model"] != d["model"]:
            raise SystemExit(
                f"camera_id={cid}: intrinsics.model={i['model']!r} vs "
                f"distortion.model={d['model']!r} — must match to recombine"
            )
        model = i["model"]
        if model not in _MODEL_LAYOUT:
            raise SystemExit(f"unsupported COLMAP model {model!r} at camera_id={cid}")
        pin_names, dist_names = _MODEL_LAYOUT[model]
        if len(d["params"]) != len(dist_names):
            raise SystemExit(
                f"camera_id={cid}: model {model} expects {len(dist_names)} distortion "
                f"params ({list(dist_names)}); got {len(d['params'])}"
            )
        # SIMPLE_* uses a single focal — assert (or coerce) fx==fy.
        if "f" in pin_names:
            if abs(i["fx"] - i["fy"]) > 1e-6:
                raise SystemExit(
                    f"camera_id={cid}: model {model} demands one focal but "
                    f"intrinsics has fx={i['fx']}, fy={i['fy']}"
                )
            pin_values = [i["fx"], i["cx"], i["cy"]]
        else:
            pin_values = [i["fx"], i["fy"], i["cx"], i["cy"]]
        params = pin_values + list(d["params"])
        lines.append(
            f"{cid} {model} {i['width']} {i['height']} " + " ".join(_fmt(v) for v in params)
        )
    return "\n".join(lines) + "\n"


def stage_images(source_dir: Path, scratch_input: Path) -> list[str]:
    """Hardlink (fallback copy) each per-cam file into ``scratch_input`` with a flat name.

    COLMAP resolves symlinks while walking ``--image_path`` (verified by
    colmap-triangulate@0.2.0 — its docstring at triangulate.py:15-22 walks
    through the same failure mode), which then pollutes stored image names
    and pushes ``image_undistorter`` outputs outside our deliverable.
    Same rig_common trick: hardlink into a clean scratch dir.
    """
    if scratch_input.exists():
        shutil.rmtree(scratch_input)
    scratch_input.mkdir(parents=True)
    names: list[str] = []
    for src in sorted(source_dir.iterdir()):
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
        names.append(src.name)
    if not names:
        raise SystemExit(f"no images staged from {source_dir}")
    return names


def write_identity_prior(
    scratch_prior: Path, cameras_txt_body: str, image_names: list[str]
) -> None:
    """image_undistorter needs a sparse model (cameras + images + points3D).

    Since this pack's typed inputs don't carry poses, we synthesise identity
    poses. Undistortion is intrinsic — extrinsics don't affect the per-pixel
    remap, so identity vs real poses produces the same pixel data. The
    written images.bin poses are effectively garbage but we don't consume
    them (this pack's output is intrinsics.json + images/, not a full
    reconstruction).
    """
    if scratch_prior.exists():
        shutil.rmtree(scratch_prior)
    scratch_prior.mkdir(parents=True)
    (scratch_prior / "cameras.txt").write_text(cameras_txt_body)
    lines = []
    # Use camera_id 1 by default. If cameras.txt has multiple ids the first
    # one wins for all images — safe under this pack's single-camera
    # assumption; multi-camera-per-shard is out of scope (recombine_cameras_txt
    # supports it, but the poses.txt author would need to know which image
    # goes to which camera, which is only in the poses input this pack
    # deliberately doesn't take).
    first_cid = int(cameras_txt_body.strip().split("\n")[0].split()[0])
    for i, name in enumerate(image_names, start=1):
        lines.append(f"{i} 1 0 0 0 0 0 0 {first_cid} {name}\n\n")
    (scratch_prior / "images.txt").write_text("".join(lines))
    (scratch_prior / "points3D.txt").write_text("")


def parse_undistorted_intrinsics(sparse_dir: Path, source_model: str) -> dict:
    """Read COLMAP's post-undistort cameras.txt back into our intrinsics schema.

    image_undistorter always emits ``PINHOLE`` (or ``SIMPLE_PINHOLE`` when
    ``fx == fy`` in the source), with fx/fy/cx/cy adjusted for the crop.
    """
    cam_txt = sparse_dir / "cameras.txt"
    if not cam_txt.is_file():
        raise SystemExit(f"expected undistorted cameras.txt at {cam_txt}")
    out_cams: list[dict] = []
    for ln in cam_txt.read_text().splitlines():
        if not ln or ln.startswith("#"):
            continue
        parts = ln.split()
        cid = int(parts[0])
        model = parts[1]
        w, h = int(parts[2]), int(parts[3])
        params = [float(x) for x in parts[4:]]
        if model == "PINHOLE":
            fx, fy, cx, cy = params
        elif model == "SIMPLE_PINHOLE":
            fx = fy = params[0]
            cx, cy = params[1], params[2]
        else:  # image_undistorter should never emit anything else
            raise SystemExit(
                f"undistorted model {model!r} at camera_id={cid} — expected "
                f"PINHOLE / SIMPLE_PINHOLE (source model was {source_model!r})"
            )
        out_cams.append(
            {
                "camera_id": cid,
                "model": model,
                "width": w,
                "height": h,
                "fx": fx,
                "fy": fy,
                "cx": cx,
                "cy": cy,
            }
        )
    return {"schema_version": 1, "cameras": out_cams}


def run(cmd: list, step: str) -> None:
    print(f"[image-undistort] {step}", flush=True)
    subprocess.run([str(x) for x in cmd], check=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--intrinsics", type=Path, required=True)
    ap.add_argument("--distortion", type=Path, required=True)
    ap.add_argument(
        "--images",
        type=Path,
        required=True,
        help="frame_sequence handle (must contain frames/ subdir).",
    )
    ap.add_argument("--pinhole-out", type=Path, required=True)
    ap.add_argument("--undistorted-out", type=Path, required=True)
    ap.add_argument("--scratch", type=Path, required=True)
    ap.add_argument("--blank-pixels", type=int, default=0)
    args = ap.parse_args()

    intr_json = args.intrinsics / "intrinsics.json"
    dist_json = args.distortion / "distortion.json"
    images_dir = args.images / "frames"
    if not intr_json.is_file():
        print(f"ERROR: missing intrinsics.json at {intr_json}", file=sys.stderr)
        return 2
    if not dist_json.is_file():
        print(f"ERROR: missing distortion.json at {dist_json}", file=sys.stderr)
        return 2
    if not images_dir.is_dir():
        print(f"ERROR: images handle missing frames/ subdir: {images_dir}", file=sys.stderr)
        return 2

    intr = json.loads(intr_json.read_text())
    dist = json.loads(dist_json.read_text())
    cameras_body = recombine_cameras_txt(intr, dist)
    source_model = intr["cameras"][0]["model"] if intr["cameras"] else "?"

    args.pinhole_out.mkdir(parents=True, exist_ok=True)
    args.undistorted_out.mkdir(parents=True, exist_ok=True)
    args.scratch.mkdir(parents=True, exist_ok=True)

    scratch_input = args.scratch / "input"
    scratch_prior = args.scratch / "prior"
    scratch_output = args.scratch / "undistorter_out"
    if scratch_output.exists():
        shutil.rmtree(scratch_output)
    scratch_output.mkdir(parents=True)

    image_names = stage_images(images_dir, scratch_input)
    write_identity_prior(scratch_prior, cameras_body, image_names)

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
        f"image_undistorter (model={source_model} -> PINHOLE, N={len(image_names)})",
    )

    # image_undistorter emits cameras.bin at sparse/ top level; move + convert.
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
        "model_converter → TXT",
    )

    # Emit our own PINHOLE camera-intrinsics view.
    pinhole_intr = parse_undistorted_intrinsics(sparse_0, source_model)
    (args.pinhole_out / "intrinsics.json").write_text(json.dumps(pinhole_intr, indent=2) + "\n")

    # Publish undistorted images at the frame_sequence layout the rest of the
    # ecosystem expects: <handle>/frames/<name>.
    out_frames = args.undistorted_out / "frames"
    if out_frames.exists():
        shutil.rmtree(out_frames)
    out_frames.mkdir(parents=True)
    src_images = scratch_output / "images"
    n_out = 0
    for src in sorted(src_images.iterdir()):
        if not src.is_file():
            continue
        dst = out_frames / src.name
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)
        n_out += 1
    if n_out != len(image_names):
        print(
            f"ERROR: undistorter produced {n_out} images, expected {len(image_names)}",
            file=sys.stderr,
        )
        return 3
    print(f"[image-undistort] done → {n_out} undistorted images + PINHOLE intrinsics")
    return 0


if __name__ == "__main__":
    sys.exit(main())
