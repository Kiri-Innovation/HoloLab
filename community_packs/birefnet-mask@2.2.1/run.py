#!/usr/bin/env python3
"""One video or ordered image array -> soft foreground alpha masks."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image


HF_REPO = "ZhengPeng7/BiRefNet"
HF_REVISION = "e2bf8e4460fc8fa32bba5ea4d94b3233d367b0e4"


def resolve_weights(checkpoint_param=None) -> Path:
    if checkpoint_param:
        path = Path(checkpoint_param).expanduser()
        if path.is_dir():
            path = path / "model.safetensors"
        if not path.is_file():
            raise FileNotFoundError(f"BiRefNet weights not found: {path}. "
                                    "Set checkpoint to a model.safetensors file or its directory.")
        return path.resolve()
    from weights import weight_path, verify
    path = weight_path()
    if not path.is_file():
        raise FileNotFoundError(f"BiRefNet weights missing: {path}. Run hololab pack setup community/birefnet-mask@2.2.1 first.")
    verify(path)
    return path


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def single_video(path):
    path = Path(path)
    if path.is_file():
        return path.resolve()
    candidates = [
        p
        for p in path.iterdir()
        if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".avi", ".mkv"}
    ]
    if len(candidates) != 1:
        raise ValueError(f"Expected exactly one video, got {len(candidates)} in {path}")
    return candidates[0].resolve()


@contextmanager
def decoded_frames(video, scratch, *, max_frames=50, skip=1,
                   start_frame=0, end_frame=0, max_width=0):
    """Own one unique scratch directory per invocation, including concurrent shards."""
    scratch.mkdir(parents=True, exist_ok=True)
    extractor_path = Path(__file__).parent / "extract_frames.py"
    extractor = load_module("birefnet_mask_extractor", extractor_path)
    with tempfile.TemporaryDirectory(prefix="birefnet-frames-", dir=scratch) as tmp:
        frames_dir = Path(tmp)
        count = extractor.extract_frames(
            video, frames_dir, frame_skip=skip, max_frames=max_frames,
            max_width=max_width, start_frame=start_frame, end_frame=end_frame,
        )
        yield frames_dir, count, extractor_path


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}


def input_kind(path):
    """Distinguish the two wire layouts; never guess for mixed handles."""
    path = Path(path)
    if path.is_file():
        return "video"
    entries = [p for p in path.iterdir() if not p.name.startswith(".")]
    if any(p.suffix.lower() in IMAGE_EXTENSIONS for p in entries):
        if any(not p.is_file() or p.suffix.lower() not in IMAGE_EXTENSIONS for p in entries):
            raise ValueError("Image array must contain only image files (no mixed/nested entries)")
        return "array"
    if not entries:
        raise ValueError("Input is empty")
    return "video"


def array_frames(path, *, max_frames=50, skip=1, start_frame=0, end_frame=0):
    """Use basename byte order, matching HoloLab arrays and images-array-source.

    Window first, stride from its start, then cap: identical index semantics to
    video extraction. Positional names and arbitrary source filenames both work.
    """
    files = sorted((p for p in path.iterdir() if not p.name.startswith(".")),
                   key=lambda p: os.fsencode(p.name))
    if any(not p.is_file() or p.suffix.lower() not in IMAGE_EXTENSIONS for p in files):
        raise ValueError("Image array must contain only image files")
    selected = files[start_frame:end_frame or None:skip][:max_frames]
    if not selected:
        raise ValueError("No image frames selected")
    return selected


def read_rgb(path, max_width=0):
    with Image.open(path) as im:
        rgb = im.convert("RGB")
        if max_width and rgb.width > max_width:
            height = max(1, round(rgb.height * max_width / rgb.width))
            rgb = rgb.resize((max_width, height), Image.Resampling.LANCZOS)
        return rgb


@contextmanager
def input_frames(path, scratch, **selection):
    kind = input_kind(path)
    if kind == "video":
        with decoded_frames(single_video(path), scratch, **selection) as (directory, count, extractor):
            files = sorted(directory.glob("*.png"))
            if not count or [f.name for f in files] != [f"p_{i:016d}.png" for i in range(count)]:
                raise ValueError("Extracted frame count/stem coverage mismatch")
            yield files, extractor, sum(f.stat().st_size for f in directory.iterdir() if f.is_file())
    else:
        # Read original frames directly; no extraction, copies or scratch frames.
        yield array_frames(path, **{k: v for k, v in selection.items() if k != "max_width"}), None, 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for arg in ["video", "output", "scratch"]:
        p.add_argument("--" + arg, required=True, type=Path)
    for arg, default in [
        ("max-frames", 50),
        ("skip", 1),
        ("start-frame", 0),
        ("end-frame", 0),
        ("max-width", 0),
    ]:
        p.add_argument("--" + arg, type=int, default=default)
    p.add_argument("--checkpoint", default="", help="Optional local safetensors file or directory")
    p.add_argument("--batch-size", type=int, default=4)
    a = p.parse_args()
    if a.batch_size < 1:
        p.error("batch-size must be positive")
    if a.max_frames < 1 or a.skip < 1 or min(a.start_frame, a.end_frame, a.max_width) < 0:
        p.error("Invalid frame selection parameters")
    if a.end_frame and a.end_frame <= a.start_frame:
        p.error("end-frame must exceed start-frame")
    kind = input_kind(a.video)
    video = single_video(a.video) if kind == "video" else a.video.resolve()
    a.output.mkdir(parents=True, exist_ok=True)
    if list(a.output.glob("*.png")):
        raise ValueError("Output already contains masks; use a fresh output directory")
    import torch
    import torch.nn.functional as F
    from torchvision import transforms
    from safetensors.torch import load_file
    from vendor.birefnet import BiRefNet

    torch.set_num_threads(4)
    started = time.monotonic()
    weight_path = resolve_weights(a.checkpoint)
    with input_frames(video, a.scratch,
                      max_frames=a.max_frames, skip=a.skip, max_width=a.max_width,
                      start_frame=a.start_frame, end_frame=a.end_frame) as (files, extractor_path, scratch_bytes):
        count = len(files)
        expected = [f"p_{i:016d}.png" for i in range(count)]
        resize_width = a.max_width if kind == "array" else 0
        records = []
        size = None
        for i, f in enumerate(files):
            with read_rgb(f, resize_width) as im:
                rgb = np.array(im)
            if size is None:
                size = rgb.shape
            if rgb.shape != size:
                raise ValueError(f"Frame dimensions changed at {f}")
            records.append(
                {
                    "name": expected[i],
                    "source_name": f.name,
                    "source_frame_index": a.start_frame + i * a.skip,
                    "rgb_sha256": hashlib.sha256(rgb.tobytes()).hexdigest(),
                }
            )
        height, width, _ = size
        model = BiRefNet(bb_pretrained=False)
        state_dict = load_file(str(weight_path))
        # Fail on incompatible overrides instead of silently using random parameters.
        model.load_state_dict(state_dict, strict=True)
        del state_dict
        model = model.eval().half().to("cuda")
        transform = transforms.Compose([
            transforms.Resize((1024, 1024)), transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
        ])
        for start in range(0, count, a.batch_size):
            batch_files = files[start:start + a.batch_size]
            tensors = []
            for f in batch_files:
                with read_rgb(f, resize_width) as im:
                    tensors.append(transform(im))
            x = torch.stack(tensors).to(device="cuda", dtype=torch.float16)
            with torch.inference_mode():
                out = model(x)
                if isinstance(out, (list, tuple)):
                    out = out[-1]
                if out.ndim != 4 or out.shape[:2] != (len(batch_files), 1):
                    raise ValueError(f"Invalid BiRefNet output shape: {out.shape}")
                if not torch.isfinite(out).all():
                    raise ValueError("Non-finite BiRefNet logits")
                probabilities = out.float().sigmoid()
                # Upsample one frame at a time to bound full-resolution memory.
                for j, probability in enumerate(probabilities):
                    alpha = F.interpolate(probability[None], size=(height, width),
                                          mode="bilinear", align_corners=False)[0, 0]
                    pixels = alpha.mul(255).round().clamp(0, 255).byte().cpu().numpy()
                    record = records[start + j]
                    dest = a.output / record["name"]
                    Image.fromarray(pixels).save(dest)
                    with Image.open(dest) as im:
                        actual = np.asarray(im)
                        if im.mode != "L" or im.size != (width, height) or not np.array_equal(actual, pixels):
                            raise ValueError(f"Mask write validation failed: {dest}")
                    record.update(foreground_ratio=float(np.mean(pixels >= 128)),
                                  alpha_mean=float(pixels.mean() / 255),
                                  soft_pixel_count=int(np.count_nonzero((pixels > 0) & (pixels < 255))),
                                  value_range=[int(pixels.min()), int(pixels.max())])
                    print(f"[ITER {start + j + 1}/{count}] BiRefNet", flush=True)
            del x, out, probabilities
        if sorted(f.name for f in a.output.glob("*.png")) != expected:
            raise ValueError("Output stem coverage mismatch")
        report = {
            "video": str(video) if kind == "video" else None,
            "input_kind": kind,
            "input": str(video),
            "model": "BiRefNet",
            "checkpoint": str(weight_path),
            "weights_source": "local" if a.checkpoint else HF_REPO,
            "weights_revision": None if a.checkpoint else HF_REVISION,
            "batch_size": a.batch_size,
            "count": count,
            "size": [width, height],
            "mode": "L",
            "value_range": [0, 255],
            "soft_alpha": True,
            "coverage_advisory_frames": [
                r["name"] for r in records if not 0.05 < r["foreground_ratio"] < 0.95
            ],
            "frames": records,
            "extractor_sha256": hashlib.sha256(extractor_path.read_bytes()).hexdigest() if extractor_path else None,
            "selection": {
                k: getattr(a, k)
                for k in ["max_frames", "skip", "start_frame", "end_frame", "max_width"]
            },
            "scratch_frame_bytes": scratch_bytes,
            "peak_gpu_allocated_gb": torch.cuda.max_memory_allocated() / 1024**3,
            "elapsed_seconds": time.monotonic() - started,
        }
        (a.output / ".validation.json").write_text(json.dumps(report, indent=2) + "\n")
        (a.output / ".hololab-validation.json").write_text(json.dumps(report, indent=2) + "\n")
        (a.output / ".hololab-done").touch()
        print(json.dumps({k: v for k, v in report.items() if k != "frames"}), flush=True)


if __name__ == "__main__":
    main()
