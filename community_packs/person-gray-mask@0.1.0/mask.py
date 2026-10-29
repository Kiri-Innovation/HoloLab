"""Gray-wall foreground masks; RGB inputs are never modified."""

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def segment(rgb):
    h, w = rgb.shape[:2]
    small = cv2.resize(rgb, (min(w, 640), round(h * min(w, 640) / w)))
    lab = cv2.cvtColor(small, cv2.COLOR_RGB2LAB).astype(np.float32)
    border = np.zeros(lab.shape[:2], bool)
    border[:, :12] = border[:, -12:] = True
    border[:12, :] = True
    # Reject skin, white clothing and dark hair from the wall palette.
    neutral = (
        (lab[..., 0] > 65)
        & (lab[..., 0] < 195)
        & (abs(lab[..., 1] - 128) < 7)
        & (abs(lab[..., 2] - 128) < 15)
    )
    samples = lab[border & neutral]
    if len(samples) < 100:
        raise ValueError("Not enough neutral gray wall on image border; use semantic segmentation")
    center = np.median(samples, axis=0)
    # Multiple wall tones are essential: shadows under the arm are much
    # darker than the upper wall but have the same neutral chroma.
    cv2.setRNGSeed(0)
    _, _, palette = cv2.kmeans(
        samples,
        5,
        None,
        (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.1),
        3,
        cv2.KMEANS_PP_CENTERS,
    )
    distance = np.min(
        np.sqrt(np.sum(((lab[..., None, :] - palette) / np.array([10.0, 2.5, 2.5])) ** 2, axis=-1)),
        axis=-1,
    )
    labels = np.where(distance < 3, cv2.GC_PR_BGD, cv2.GC_PR_FGD).astype("uint8")
    labels[distance < 1.7] = cv2.GC_BGD
    labels[distance > 5] = cv2.GC_FGD
    cv2.grabCut(small, labels, None, np.zeros((1, 65)), np.zeros((1, 65)), 3, cv2.GC_INIT_WITH_MASK)
    mask = ((labels == cv2.GC_FGD) | (labels == cv2.GC_PR_FGD)).astype("uint8")
    count, ids, stats, _ = cv2.connectedComponentsWithStats(mask)
    if count < 2:
        raise ValueError("Empty foreground")
    mask = (ids == (1 + np.argmax(stats[1:, cv2.CC_STAT_AREA]))).astype("uint8") * 255
    # Close only tiny pinholes; preserve the gaps between fingers and arm/body.
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    return cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST), center.tolist()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--colmap", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--report", type=Path, required=True)
    a = p.parse_args()
    files = sorted(
        f for f in (a.colmap / "images").iterdir() if f.suffix.lower() in {".png", ".jpg", ".jpeg"}
    )
    if not files or len({f.stem for f in files}) != len(files):
        raise ValueError("Missing images or duplicate stems")
    a.output.mkdir(parents=True, exist_ok=True)
    a.report.mkdir(parents=True, exist_ok=True)
    records = []
    cv2.setNumThreads(2)
    for i, f in enumerate(files):
        bgr = cv2.imread(str(f), cv2.IMREAD_COLOR)
        if bgr is None:
            raise ValueError(f"Unreadable image: {f}")
        mask, color = segment(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        coverage = float((mask > 0).mean())
        if not 0.05 < coverage < 0.95:
            raise ValueError(f"Implausible foreground coverage: {f}: {coverage}")
        dest = a.output / (f.stem + ".png")
        if not cv2.imwrite(str(dest), mask):
            raise OSError(dest)
        records.append(dict(image=f.name, mask=dest.name, coverage=coverage, wall_lab=color))
        if i in {0, len(files) // 2, len(files) - 1}:
            checker = np.indices(mask.shape).sum(axis=0) // 24 % 2 * 30 + 200
            composite = np.where(
                mask[..., None] > 0, bgr, np.repeat(checker[..., None], 3, axis=2)
            ).astype("uint8")
            panel = np.concatenate([bgr, cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR), composite], axis=1)
            cv2.imwrite(str(a.report / (f"sample_{i:03d}.png")), panel)
        (a.report / "progress.json").write_text(json.dumps(records, indent=2))
        print(f"[MASK {i + 1}/{len(files)}] {f.name} coverage={coverage:.4f}", flush=True)
    expected = {f.stem + ".png" for f in files}
    if {f.name for f in a.output.glob("*.png")} != expected:
        raise ValueError("Mask/image stem mismatch")
    for f in files:
        mask = cv2.imread(str(a.output / (f.stem + ".png")), 0)
        source = cv2.imread(str(f))
        if mask.shape != source.shape[:2] or not set(np.unique(mask)) <= {0, 255}:
            raise ValueError(f"Invalid mask {f}")
    (a.report / "validation.json").write_text(
        json.dumps(dict(valid=True, count=len(files), records=records), indent=2)
    )
    (a.output / ".done").touch()


if __name__ == "__main__":
    main()
