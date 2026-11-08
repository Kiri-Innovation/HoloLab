#!/usr/bin/env python3
"""One video or ordered image array -> soft foreground alpha masks."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import tempfile
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import numpy as np
from PIL import Image

HF_REPO = "ZhengPeng7/BiRefNet"
HF_REVISION = "e2bf8e4460fc8fa32bba5ea4d94b3233d367b0e4"


def resolve_weights(checkpoint_param=None) -> Path:
    # Machine-local override only; never serialized into workflow params.
    checkpoint_param = checkpoint_param or os.environ.get("BIREFNET_CHECKPOINT")
    if checkpoint_param:
        path = Path(checkpoint_param).expanduser()
        if path.is_dir():
            path = path / "model.safetensors"
        if not path.is_file():
            raise FileNotFoundError(
                f"BiRefNet weights not found: {path}. "
                "Set checkpoint to a model.safetensors file or its directory."
            )
        return path.resolve()
    from weights import verify, weight_path

    path = weight_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"BiRefNet weights missing: {path}. Run hololab pack setup community/birefnet-mask@2.2.5 first."
        )
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
def decoded_frames(
    video, scratch, *, max_frames=50, skip=1, start_frame=0, end_frame=0, max_width=0
):
    """Own one unique scratch directory per invocation, including concurrent shards."""
    scratch.mkdir(parents=True, exist_ok=True)
    extractor_path = Path(__file__).parent / "extract_frames.py"
    extractor = load_module("birefnet_mask_extractor", extractor_path)
    with tempfile.TemporaryDirectory(prefix="birefnet-frames-", dir=scratch) as tmp:
        frames_dir = Path(tmp)
        count = extractor.extract_frames(
            video,
            frames_dir,
            frame_skip=skip,
            max_frames=max_frames,
            max_width=max_width,
            start_frame=start_frame,
            end_frame=end_frame,
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
    files = sorted(
        (p for p in path.iterdir() if not p.name.startswith(".")), key=lambda p: os.fsencode(p.name)
    )
    if any(not p.is_file() or p.suffix.lower() not in IMAGE_EXTENSIONS for p in files):
        raise ValueError("Image array must contain only image files")
    selected = files[start_frame : end_frame or None : skip][:max_frames]
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
        with decoded_frames(single_video(path), scratch, **selection) as (
            directory,
            count,
            extractor,
        ):
            files = sorted(directory.glob("*.png"))
            if not count or [f.name for f in files] != [f"p_{i:016d}.png" for i in range(count)]:
                raise ValueError("Extracted frame count/stem coverage mismatch")
            yield (
                files,
                extractor,
                sum(f.stat().st_size for f in directory.iterdir() if f.is_file()),
            )
    else:
        # Read original frames directly; no extraction, copies or scratch frames.
        yield (
            array_frames(path, **{k: v for k, v in selection.items() if k != "max_width"}),
            None,
            0,
        )


def prepare_frame(index, path, *, transform, max_width, start_frame, skip):
    """Decode once; hash native RGB before resizing, then retain only a tensor."""
    with read_rgb(path) as native:
        rgb = np.asarray(native)
        digest = hashlib.sha256(rgb.tobytes()).hexdigest()
        source_size = native.size
        if max_width and native.width > max_width:
            height = max(1, round(native.height * max_width / native.width))
            image = native.resize((max_width, height), Image.Resampling.LANCZOS)
        else:
            image = native
        try:
            size = image.size
            tensor = transform(image)
        finally:
            if image is not native:
                image.close()
    record = {
        "name": f"p_{index:016d}.png",
        "source_name": path.name,
        "source_frame_index": start_frame + index * skip,
        "rgb_sha256": digest,
    }
    return tensor, record, size, source_size


@contextmanager
def prefetched_frames(files, *, depth=4, **kwargs):
    """One producer, at most depth submitted frames; no whole-video RGB cache.

    Only CPU tensors and metadata are retained by futures. Exceptions propagate
    to the consumer; shutdown joins the producer even if inference fails.
    """
    if depth < 1:
        raise ValueError("prefetch depth must be positive")
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="birefnet-decode") as pool:
        pending = deque()
        source = iter(enumerate(files))

        def submit_one():
            item = next(source, None)
            if item is not None:
                index, path = item
                pending.append(pool.submit(prepare_frame, index, path, **kwargs))

        for _ in range(depth):
            submit_one()

        def consume():
            while pending:
                result = pending.popleft().result()
                submit_one()
                yield result

        try:
            yield consume()
        finally:
            for future in pending:
                future.cancel()


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
    p.add_argument("--model-input-size", type=int, choices=[512, 768, 1024], default=1024)
    p.add_argument("--compress-level", type=int, choices=range(10), default=1)
    a = p.parse_args()
    if a.batch_size < 1:
        p.error("batch-size must be positive")
    if a.max_frames < 1 or a.skip < 1 or min(a.start_frame, a.end_frame, a.max_width) < 0:
        p.error("Invalid frame selection parameters")
    if a.end_frame and a.end_frame <= a.start_frame:
        p.error("end-frame must exceed start-frame")
    kind = input_kind(a.video)
    video = single_video(a.video) if kind == "video" else a.video.resolve()
    print(f"[INPUT] {kind}: {video}", flush=True)
    a.output.mkdir(parents=True, exist_ok=True)
    if list(a.output.glob("*.png")):
        raise ValueError("Output already contains masks; use a fresh output directory")
    import torch
    import torch.nn.functional as F
    from safetensors.torch import load_file
    from torchvision import transforms
    from vendor.birefnet import BiRefNet

    torch.set_num_threads(4)
    started = time.monotonic()
    checkpoint_override = a.checkpoint or os.environ.get("BIREFNET_CHECKPOINT")
    weight_path = resolve_weights(checkpoint_override)
    with input_frames(
        video,
        a.scratch,
        max_frames=a.max_frames,
        skip=a.skip,
        max_width=0,  # Hash native RGB; resize only after hashing in prepare_frame.
        start_frame=a.start_frame,
        end_frame=a.end_frame,
    ) as (files, extractor_path, scratch_bytes):
        count = len(files)
        expected = [f"p_{i:016d}.png" for i in range(count)]
        records = []
        size = None
        model = BiRefNet(bb_pretrained=False)
        state_dict = load_file(str(weight_path))
        # Fail on incompatible overrides instead of silently using random parameters.
        model.load_state_dict(state_dict, strict=True)
        del state_dict
        model = model.eval().half().to("cuda")
        transform = transforms.Compose(
            [
                transforms.Resize((a.model_input_size, a.model_input_size)),
                transforms.ToTensor(),
                transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
            ]
        )
        with prefetched_frames(
            files,
            depth=4,
            transform=transform,
            max_width=a.max_width,
            start_frame=a.start_frame,
            skip=a.skip,
        ) as prepared:
            for start in range(0, count, a.batch_size):
                tensors = []
                for _ in range(min(a.batch_size, count - start)):
                    tensor, record, frame_size, source_size = next(prepared)
                    if size is None:
                        size = frame_size
                        native_size = source_size
                    if frame_size != size or source_size != native_size:
                        raise ValueError("Frame dimensions changed")
                    records.append(record)
                    tensors.append(tensor)
                width, height = size
                x = torch.stack(tensors).to(device="cuda", dtype=torch.float16)
                with torch.inference_mode():
                    out = model(x)
                    if isinstance(out, (list, tuple)):
                        out = out[-1]
                    if out.ndim != 4 or out.shape[:2] != (len(tensors), 1):
                        raise ValueError(f"Invalid BiRefNet output shape: {out.shape}")
                    if not torch.isfinite(out).all():
                        raise ValueError("Non-finite BiRefNet logits")
                    probabilities = out.float().sigmoid()
                    # Upsample one frame at a time to bound full-resolution memory.
                    for j, probability in enumerate(probabilities):
                        alpha = F.interpolate(
                            probability[None],
                            size=(height, width),
                            mode="bilinear",
                            align_corners=False,
                        )[0, 0]
                        pixels = alpha.mul(255).round().clamp(0, 255).byte().cpu().numpy()
                        record = records[start + j]
                        dest = a.output / record["name"]
                        Image.fromarray(pixels).save(dest, compress_level=a.compress_level)
                        with Image.open(dest) as im:
                            actual = np.asarray(im)
                            if (
                                im.mode != "L"
                                or im.size != (width, height)
                                or not np.array_equal(actual, pixels)
                            ):
                                raise ValueError(f"Mask write validation failed: {dest}")
                        record.update(
                            foreground_ratio=float(np.mean(pixels >= 128)),
                            alpha_mean=float(pixels.mean() / 255),
                            soft_pixel_count=int(np.count_nonzero((pixels > 0) & (pixels < 255))),
                            value_range=[int(pixels.min()), int(pixels.max())],
                        )
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
            "weights_source": "local" if checkpoint_override else HF_REPO,
            "weights_revision": None if checkpoint_override else HF_REVISION,
            "batch_size": a.batch_size,
            "model_input_size": a.model_input_size,
            "compress_level": a.compress_level,
            "prefetch_depth": 4,
            "source_size": list(native_size),
            "count": count,
            "size": [width, height],
            "mode": "L",
            "value_range": [0, 255],
            "soft_alpha": True,
            "coverage_advisory_frames": [
                r["name"] for r in records if not 0.05 < r["foreground_ratio"] < 0.95
            ],
            "frames": records,
            "extractor_sha256": hashlib.sha256(extractor_path.read_bytes()).hexdigest()
            if extractor_path
            else None,
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
