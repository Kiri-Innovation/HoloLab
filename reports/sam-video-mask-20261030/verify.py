"""Read all mask PNGs and compare recorded inference RGB hashes against existing frame artifacts."""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image

p = argparse.ArgumentParser()
p.add_argument("masks", type=Path)
p.add_argument("reference", type=Path)
p.add_argument("--arrayed", action="store_true")
p.add_argument("--save", type=Path, required=True)
a = p.parse_args()
roots = (
    sorted(x for x in a.masks.iterdir() if not x.name.startswith(".")) if a.arrayed else [a.masks]
)
if a.arrayed:
    assert len(roots) == 80, len(roots)
results = []
for root in roots:
    ref = a.reference / root.name if a.arrayed else a.reference
    report = json.loads((root / ".validation.json").read_text())
    masks = sorted(root.glob("*.png"))
    frames = sorted(ref.glob("*.png"))
    assert len(masks) == len(frames) == report["count"] == 50, root
    assert [x.name for x in masks] == [x.name for x in frames]
    ratios = []
    for mask, frame, record in zip(masks, frames, report["frames"], strict=True):
        assert record["name"] == mask.name
        with Image.open(frame) as image:
            rgb = np.asarray(image.convert("RGB"))
            assert hashlib.sha256(rgb.tobytes()).hexdigest() == record["rgb_sha256"], (root, frame)
            size = image.size
        with Image.open(mask) as image:
            data = np.asarray(image)
            assert image.mode == "L" and image.size == size
            assert data.dtype == np.uint8 and np.isin(data, [0, 255]).all()
            ratio = float(np.count_nonzero(data) / data.size)
            assert 0.0 < ratio < 1.0, (mask, ratio)
            ratios.append(ratio)
    results.append(
        {
            "element": root.name,
            "video": report["video"],
            "count": len(masks),
            "size": size,
            "mode": "L",
            "values": [0, 255],
            "foreground_min": min(ratios),
            "foreground_max": max(ratios),
            "all_rgb_hashes_match": True,
            "foreground_per_frame": ratios,
        }
    )
    print(root.name, len(masks), size, min(ratios), max(ratios), flush=True)
a.save.write_text(json.dumps(results, indent=2) + "\n")
