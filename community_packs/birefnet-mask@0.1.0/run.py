#!/usr/bin/env python3
"""One video -> soft foreground alpha masks, using the exact HoloLab frame extractor."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image


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


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for arg in ["video", "output", "scratch", "checkpoint", "hololab-root"]:
        p.add_argument("--" + arg, required=True, type=Path)
    for arg, default in [
        ("max-frames", 50),
        ("skip", 1),
        ("start-frame", 0),
        ("end-frame", 0),
        ("max-width", 0),
    ]:
        p.add_argument("--" + arg, type=int, default=default)
    p.add_argument(
        "--reference-frames",
        default="",
        help="Optional existing extraction for exact per-frame pixel validation.",
    )
    p.add_argument("--batch-size", type=int, default=4)
    a = p.parse_args()
    a.reference_frames = Path(a.reference_frames) if a.reference_frames else None
    if a.batch_size < 1:
        p.error("batch-size must be positive")
    if a.max_frames < 1 or a.skip < 1 or min(a.start_frame, a.end_frame, a.max_width) < 0:
        p.error("Invalid frame selection parameters")
    if a.end_frame and a.end_frame <= a.start_frame:
        p.error("end-frame must exceed start-frame")
    video = single_video(a.video)
    if not a.checkpoint.is_dir():
        raise FileNotFoundError(a.checkpoint)
    a.output.mkdir(parents=True, exist_ok=True)
    if list(a.output.glob("*.png")):
        raise ValueError("Output already contains masks; use a fresh output directory")
    a.scratch.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(a.hololab_root))
    import hololab

    extractor_path = (
        Path(hololab.__file__).parent / "builtin_packs/frame-extraction@0.2.0/extract_frames.py"
    )
    extractor = load_module("birefnet_mask_extractor", extractor_path)
    import torch
    import torch.nn.functional as F
    from torchvision import transforms
    from transformers import AutoModelForImageSegmentation

    torch.set_num_threads(4)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="birefnet-frames-", dir=a.scratch) as tmp:
        frames_dir = Path(tmp)
        count = extractor.extract_frames(
            video,
            frames_dir,
            frame_skip=a.skip,
            max_frames=a.max_frames,
            max_width=a.max_width,
            start_frame=a.start_frame,
            end_frame=a.end_frame,
        )
        files = sorted(frames_dir.glob("*.png"))
        expected = [f"p_{i:016d}.png" for i in range(count)]
        if not count or [f.name for f in files] != expected:
            raise ValueError("Extracted frame count/stem coverage mismatch")
        if (
            a.reference_frames
            and sorted(f.name for f in a.reference_frames.glob("*.png")) != expected
        ):
            raise ValueError("Reference frame count/stem coverage mismatch")
        records = []
        size = None
        for i, f in enumerate(files):
            with Image.open(f) as im:
                rgb = np.array(im.convert("RGB"))
            if size is None:
                size = rgb.shape
            if rgb.shape != size:
                raise ValueError(f"Frame dimensions changed at {f}")
            if a.reference_frames:
                with Image.open(a.reference_frames / f.name) as ref:
                    if not np.array_equal(rgb, np.asarray(ref.convert("RGB"))):
                        raise ValueError(f"Reference frame pixel mismatch: {f.name}")
            records.append(
                {
                    "name": f.name,
                    "source_frame_index": a.start_frame + i * a.skip,
                    "rgb_sha256": hashlib.sha256(rgb.tobytes()).hexdigest(),
                }
            )
        height, width, _ = size
        model = AutoModelForImageSegmentation.from_pretrained(
            str(a.checkpoint), local_files_only=True, trust_remote_code=True
        ).eval().half().to("cuda")
        transform = transforms.Compose([
            transforms.Resize((1024, 1024)), transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
        ])
        for start in range(0, count, a.batch_size):
            batch_files = files[start:start + a.batch_size]
            tensors = []
            for f in batch_files:
                with Image.open(f) as im:
                    tensors.append(transform(im.convert("RGB")))
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
            "video": str(video),
            "model": "BiRefNet",
            "checkpoint": str(a.checkpoint),
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
            "extractor_sha256": hashlib.sha256(extractor_path.read_bytes()).hexdigest(),
            "selection": {
                k: getattr(a, k)
                for k in ["max_frames", "skip", "start_frame", "end_frame", "max_width"]
            },
            "reference_pixels_checked": a.reference_frames is not None,
            "peak_gpu_allocated_gb": torch.cuda.max_memory_allocated() / 1024**3,
            "elapsed_seconds": time.monotonic() - started,
        }
        (a.output / ".validation.json").write_text(json.dumps(report, indent=2) + "\n")
        (a.output / ".hololab-done").touch()
        print(json.dumps({k: v for k, v in report.items() if k != "frames"}), flush=True)


if __name__ == "__main__":
    main()
