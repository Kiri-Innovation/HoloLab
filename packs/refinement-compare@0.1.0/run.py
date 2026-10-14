"""Same-view diagnostics against unchanged observations; no claimed held-out accuracy."""

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

p = argparse.ArgumentParser()
for k in ["before", "after", "fixed", "references", "masks", "output"]:
    p.add_argument("--" + k, type=Path, required=True)
a = p.parse_args()
a.output.mkdir(parents=True, exist_ok=True)


def rgb(p):
    return np.asarray(Image.open(p).convert("RGB"), dtype=np.float32) / 255


refs = {x.stem: x for x in a.references.glob("*.png")}


def find(root, key):
    candidates = [
        x
        for x in root.rglob("*")
        if x.is_file()
        and (x.stem == key or key in x.relative_to(root).parts[:-1])
        and not x.name.startswith(".")
    ]
    assert len(candidates) == 1, (root, key, candidates)
    return candidates[0]


def metrics(im, gt, mask):
    error = (im - gt) ** 2
    lap = cv2.Laplacian(cv2.cvtColor(im, cv2.COLOR_RGB2GRAY), cv2.CV_32F)
    interior = cv2.erode(mask.astype("uint8"), np.ones((7, 7), np.uint8)).astype(bool)
    outside = ~cv2.dilate(mask.astype("uint8"), np.ones((11, 11), np.uint8)).astype(bool)
    assert interior.any() and outside.any(), "Mask has no usable interior/background"
    return dict(
        interior_laplacian_variance=float(lap[interior].var()),
        outside_mask_rgb_mean=float(im[outside].mean()),
        psnr=float(-10 * np.log10(max(float(error.mean()), 1e-12))),
        foreground_psnr=float(-10 * np.log10(max(float(error[mask].mean()), 1e-12))),
        foreground_laplacian_variance=float(lap[mask].var()),
    )


rows = []
for i, (stem, path) in enumerate(sorted(refs.items())):
    gt = rgb(path)
    before = rgb(find(a.before, stem))
    after = rgb(find(a.after, stem))
    fixed = rgb(find(a.fixed, stem))
    assert before.shape == after.shape == fixed.shape == gt.shape
    mask = (
        np.asarray(
            Image.open(a.masks / (stem + ".png"))
            .convert("L")
            .resize((gt.shape[1], gt.shape[0]), Image.Resampling.NEAREST)
        )
        > 127
    )
    row = dict(
        stem=stem,
        before=metrics(before, gt, mask),
        after=metrics(after, gt, mask),
        fixed=metrics(fixed, gt, mask),
    )
    rows.append(row)
    if i in [0, len(refs) // 4, len(refs) // 2, 3 * len(refs) // 4, len(refs) - 1]:
        canvas = Image.new("RGB", (650 * 4, 690), (25, 25, 25))
        d = ImageDraw.Draw(canvas)
        for j, (im, label) in enumerate(
            [
                (gt, "Original observation"),
                (before, "Before: frozen t=0"),
                (fixed, "Difix target"),
                (after, "After: 3000 iterations"),
            ]
        ):
            canvas.paste(
                Image.fromarray((im * 255).round().astype("uint8")).resize((650, 650)),
                (j * 650, 40),
            )
            d.text((j * 650 + 10, 12), label, fill="white")
        canvas.save(a.output / (stem + ".png"))
    (a.output / "per_view.json").write_text(json.dumps(rows, indent=2))
mean = {
    stage: {k: float(np.mean([r[stage][k] for r in rows])) for k in rows[0][stage]}
    for stage in ["before", "fixed", "after"]
}
view_deltas = sorted(
    [(r["stem"], r["after"]["foreground_psnr"] - r["before"]["foreground_psnr"]) for r in rows],
    key=lambda pair: pair[1],
)
(a.output / "summary.json").write_text(
    json.dumps(
        dict(
            views=len(rows),
            improved_foreground_views=sum(delta > 0 for _, delta in view_deltas),
            worst_foreground_delta=view_deltas[0],
            best_foreground_delta=view_deltas[-1],
            mean=mean,
            delta={k: mean["after"][k] - mean["before"][k] for k in mean["before"]},
            caveat="Training-view diagnostics against original observations, not held-out quality; Laplacian can increase with artifacts.",
        ),
        indent=2,
    )
)
print(json.dumps(mean, indent=2))
