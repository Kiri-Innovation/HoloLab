#!/usr/bin/env python3
"""One video -> frame masks, using HoloLab extraction and Vista4D's SAM3 wrapper."""

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


class CheckedPredictor:
    """Validate the wrapper's frame protocol instead of silently leaving missing masks zero."""

    def __init__(self, predictor, count, height, width):
        self.predictor = predictor
        self.count, self.height, self.width = count, height, width
        self.session = None
        self.seen = set()

    def handle_request(self, request):
        result = self.predictor.handle_request(request)
        if request["type"] == "start_session":
            self.session = result["session_id"]
        elif request["type"] == "close_session":
            self.session = None
        return result

    def handle_stream_request(self, request):
        for result in self.predictor.handle_stream_request(request):
            i = result["frame_index"]
            if i not in range(self.count) or i in self.seen:
                raise ValueError(f"Duplicate/out-of-range SAM3 frame index: {i}")
            self.seen.add(i)
            outputs = result["outputs"]
            if outputs is None:
                raise ValueError(f"Missing SAM3 outputs for frame {i}")
            masks = outputs["out_binary_masks"]
            if len(masks) and (
                masks.dtype != np.bool_ or masks.shape[1:] != (self.height, self.width)
            ):
                raise ValueError(f"Invalid SAM3 mask dtype/shape: {masks.dtype} {masks.shape}")
            print(f"[ITER {len(self.seen)}/{self.count}] SAM3 frame={i}", flush=True)
            yield result
        if self.seen != set(range(self.count)):
            raise ValueError(f"SAM3 frame coverage mismatch: {sorted(self.seen)}")

    def close(self):
        if self.session is not None:
            self.predictor.handle_request({"type": "close_session", "session_id": self.session})
            self.session = None


def validate_mask(path, size):
    with Image.open(path) as im:
        pixels = np.asarray(im)
        if im.mode != "L" or im.size != size or pixels.dtype != np.uint8:
            raise ValueError(f"Mask mode/size/dtype mismatch: {path}")
        if not np.isin(pixels, [0, 255]).all():
            raise ValueError(f"Nonbinary mask: {path}")
        ratio = float(np.count_nonzero(pixels) / pixels.size)
        if not 0.0 < ratio < 1.0:
            raise ValueError(f"Degenerate empty/full person mask: {path}: {ratio}")
        return ratio


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for arg in ["video", "output", "scratch", "vista-root", "checkpoint", "hololab-root"]:
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
        type=Path,
        help="Optional existing extraction for exact per-frame pixel validation.",
    )
    a = p.parse_args()
    if a.max_frames < 1 or a.skip < 1 or min(a.start_frame, a.end_frame, a.max_width) < 0:
        p.error("Invalid frame selection parameters")
    if a.end_frame and a.end_frame <= a.start_frame:
        p.error("end-frame must exceed start-frame")
    video = single_video(a.video)
    if not a.checkpoint.is_file():
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
    extractor = load_module("sam_mask_extractor", extractor_path)
    sys.path.insert(0, str(a.vista_root))
    wrapper = load_module(
        "vista_sam3_official", a.vista_root / "utils/recon_and_seg/seg_sam3_official.py"
    )
    import torch
    from sam3.model_builder import build_sam3_video_predictor

    torch.set_num_threads(4)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="sam-frames-", dir=a.scratch) as tmp:
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
        frames = None
        for i, f in enumerate(files):
            with Image.open(f) as im:
                rgb = np.array(im.convert("RGB"))
            if frames is None:
                frames = np.empty((count, *rgb.shape), dtype=np.uint8)
            if rgb.shape != frames.shape[1:]:
                raise ValueError(f"Frame dimensions changed at {f}")
            if a.reference_frames:
                with Image.open(a.reference_frames / f.name) as ref:
                    if not np.array_equal(rgb, np.asarray(ref.convert("RGB"))):
                        raise ValueError(f"Reference frame pixel mismatch: {f.name}")
            frames[i] = rgb
            records.append(
                {
                    "name": f.name,
                    "source_frame_index": a.start_frame + i * a.skip,
                    "rgb_sha256": hashlib.sha256(rgb.tobytes()).hexdigest(),
                }
            )
        _, height, width, _ = frames.shape
        predictor = build_sam3_video_predictor(checkpoint_path=str(a.checkpoint), gpus_to_use=[0])
        checked = CheckedPredictor(predictor, count, height, width)
        try:
            masks, segments = wrapper.run_sam3_video(frames, checked, ["person"])
            del segments, frames
            if masks.dtype != np.bool_ or masks.shape != (count, height, width):
                raise ValueError(
                    f"SAM3 aggregate shape/dtype mismatch: {masks.shape}, {masks.dtype}"
                )
            for record, mask in zip(records, masks, strict=True):
                dest = a.output / record["name"]
                Image.fromarray(mask.astype(np.uint8) * 255).save(dest)
                record["foreground_ratio"] = validate_mask(dest, (width, height))
            if sorted(f.name for f in a.output.glob("*.png")) != expected:
                raise ValueError("Output stem coverage mismatch")
        finally:
            checked.close()
            predictor.shutdown()
        report = {
            "video": str(video),
            "prompt": "person",
            "count": count,
            "size": [width, height],
            "mode": "L",
            "values": [0, 255],
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
