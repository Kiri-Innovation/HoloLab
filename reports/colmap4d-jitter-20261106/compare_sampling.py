"""Offline algorithm comparison on one real source. Does not test live transport.

Run with colmap4d source on PYTHONPATH, NumPy/Matplotlib available. Large temporary
sample tables stay outside git; only figures and evidence are written to the report.
"""

import hashlib
import json
import math
import resource
import sys
import time
from pathlib import Path
from unittest.mock import patch

import colmap4d.preview as preview
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
from colmap4d.arrays import read_point_arrays
from colmap4d.sidecar import read_points_t_bin

matplotlib.use("Agg")

ROOT = Path(sys.argv[1])
OUT = Path(sys.argv[2])
TEMP = Path(sys.argv[3])
OUT.mkdir(parents=True, exist_ok=True)
TEMP.mkdir(parents=True, exist_ok=True)
BUDGET = 200_000
page = preview.read_camera_page(ROOT)
first = next(iter(page.images.values()))
cam = page.cameras[first.camera_id]
first_ns = page.times[first.id]
cameras = preview.preview_files(ROOT, part="cameras")
(TEMP / "cameras").mkdir(exist_ok=True)
for name, data in cameras.files.items():
    (TEMP / "cameras" / name).write_bytes(data)


def old_front(path, *, budget, seed):
    del seed
    return preview._point_batch(path, 0, lambda n: list(range(0, n, max(1, math.ceil(n / budget)))))


def old_library(path, *, budget, seed):
    del seed

    def indices(n):
        take = min(n, budget)
        return [n // 2] if take == 1 else [i * (n - 1) // (take - 1) for i in range(take)]

    return preview._point_batch(path, 0, indices)


metrics = {
    "source": str(ROOT.resolve()),
    "budget": BUDGET,
    "seed": 0,
    "source_stat": {
        name: {"bytes": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns}
        for name in ["points3D.bin", "points_t.bin"]
        for p in [ROOT / "sparse" / "0" / name]
    },
    "cases": {},
}
for key, replacement in [
    ("old_frontend", old_front),
    ("old_library", old_library),
    ("jitter", None),
]:
    start = time.monotonic()
    if replacement:
        with patch("colmap4d.preview.sample_points", side_effect=replacement):
            p = preview.preview_files(ROOT, part="points", budget=BUDGET, seed=0)
    else:
        p = preview.preview_files(ROOT, part="points", budget=BUDGET, seed=0)
    directory = TEMP / key
    directory.mkdir(exist_ok=True)
    for name, data in p.files.items():
        (directory / name).write_bytes(data)
    metrics["cases"][key] = {
        "total": p.total,
        "shown": p.shown,
        "seconds": time.monotonic() - start,
        "sha256": {name: hashlib.sha256(data).hexdigest() for name, data in p.files.items()},
    }
    print(key, json.dumps(metrics["cases"][key]), flush=True)
    del p

start = time.monotonic()
repeat = preview.preview_files(ROOT, part="points", budget=BUDGET, seed=0)
repeat_hashes = {name: hashlib.sha256(data).hexdigest() for name, data in repeat.files.items()}
assert repeat_hashes == metrics["cases"]["jitter"]["sha256"]
metrics["repeat"] = {
    "identical_files": True,
    "sha256": repeat_hashes,
    "seconds": time.monotonic() - start,
}
del repeat

# Reproject selected XYZ into the actual first COLMAP camera; this is not a
# fabricated raster dataset or a proxy for source-row index plots.
w, x, y, z = first.qvec
rotation = np.array(
    [
        [1 - 2 * y * y - 2 * z * z, 2 * x * y - 2 * w * z, 2 * x * z + 2 * w * y],
        [2 * x * y + 2 * w * z, 1 - 2 * x * x - 2 * z * z, 2 * y * z - 2 * w * x],
        [2 * x * z - 2 * w * y, 2 * y * z + 2 * w * x, 1 - 2 * x * x - 2 * y * y],
    ]
)
fx, fy, cx, cy = cam.params
plots = []
for key in metrics["cases"]:
    ids, xyz, rgb = read_point_arrays(TEMP / key / "points3D.bin")
    times = read_points_t_bin(TEMP / key / "points_t.bin")
    selected = np.fromiter((times.get(int(i)) == first_ns for i in ids), dtype=bool)
    camera_xyz = xyz[selected] @ rotation.T + np.asarray(first.tvec)
    uv = camera_xyz[:, :2] / camera_xyz[:, 2, None] * [fx, fy] + [cx, cy]
    valid = (
        (camera_xyz[:, 2] > 0)
        & (uv[:, 0] >= 0)
        & (uv[:, 0] < cam.width)
        & (uv[:, 1] >= 0)
        & (uv[:, 1] < cam.height)
    )
    uv, color = uv[valid], rgb[selected][valid] / 255
    # Real source is a half-resolution dense grid; the same column bins apply
    # to all three projections and do not alter any rendered point positions.
    hist = np.bincount(
        np.clip(np.rint(uv[:, 0] / 2).astype(int), 0, cam.width // 2 - 1), minlength=cam.width // 2
    )
    metrics["cases"][key].update(
        {
            "first_frame_points": len(uv),
            "occupied_halfres_columns": int(np.count_nonzero(hist)),
            "column_count_cv": float(hist.std() / hist.mean()),
        }
    )
    plots.append((key, uv, color))

plt.rcParams.update({"font.size": 11, "text.color": "#eeeeee", "axes.labelcolor": "#eeeeee"})
fig, axes = plt.subplots(1, 3, figsize=(18, 4.6), dpi=180, facecolor="#101419")
titles = {
    "old_frontend": "Old frontend: fixed stride 210",
    "old_library": "a8a5139: fixed evenly spaced ranks",
    "jitter": "New: stratified jitter, seed=0",
}
for axis, (key, uv, color) in zip(axes, plots, strict=True):
    axis.set_facecolor("#171b21")
    axis.scatter(uv[:, 0], uv[:, 1], c=color, s=3.2, linewidths=0, alpha=1, rasterized=True)
    axis.set(xlim=(0, cam.width), ylim=(cam.height, 0), aspect="equal")
    axis.set_xticks([])
    axis.set_yticks([])
    axis.set_title(titles[key], color="#eeeeee", pad=12)
    for spine in axis.spines.values():
        spine.set_color("#55606b")
    stats = metrics["cases"][key]
    axis.set_xlabel(
        f"{stats['first_frame_points']:,} first-frame points | {stats['occupied_halfres_columns']}/960 columns occupied",
        labelpad=12,
    )
fig.suptitle(
    "Same real 41,990,400-point colmap4d | identical first-camera projection and point size",
    y=0.98,
    fontsize=14,
)
fig.text(
    0.5,
    0.04,
    "Budget: 200,000 over all 81 frames. Showing t=0 only to reveal sampling gaps; no interpolation, pixel filling or extra points.",
    ha="center",
    color="#b7c4d2",
    fontsize=10,
)
fig.subplots_adjust(left=0.015, right=0.985, top=0.86, bottom=0.17, wspace=0.045)
fig.savefig(OUT / "projection-comparison.png", facecolor=fig.get_facecolor())
# A direct before/after pair, at higher display scale, for the user-visible bug.
fig2, axes2 = plt.subplots(1, 2, figsize=(14, 5), dpi=180, facecolor="#101419")
for axis, (key, uv, color) in zip(axes2, [plots[0], plots[2]], strict=True):
    axis.set_facecolor("#171b21")
    axis.scatter(uv[:, 0], uv[:, 1], c=color, s=4.5, linewidths=0)
    axis.set(xlim=(0, cam.width), ylim=(cam.height, 0), aspect="equal")
    axis.set_xticks([])
    axis.set_yticks([])
    axis.set_title(titles[key], color="#eeeeee", pad=12)
fig2.suptitle("Real point cloud, same camera / t=0 / budget / point size", y=0.98, fontsize=14)
fig2.subplots_adjust(left=0.015, right=0.985, top=0.9, bottom=0.04, wspace=0.03)
fig2.savefig(OUT / "before-after.png", facecolor=fig2.get_facecolor())
metrics["projection"] = {
    "camera_id": cam.id,
    "image_id": first.id,
    "time_ns": first_ns,
    "width": cam.width,
    "height": cam.height,
    "camera_view": cameras.camera_bounds,
}
metrics["max_rss_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
(OUT / "evidence.json").write_text(json.dumps(metrics, indent=2))
print(json.dumps(metrics, indent=2), flush=True)
