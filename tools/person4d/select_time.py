"""Select a frozen time from original-frame evidence; resume each completed time batch.

Default objective: reference-camera foreground PSNR. The 93-camera control uses
an arithmetic mean of per-camera foreground PSNR (not pooled MSE).
Run with the fastgs Python, directly or through evaluate.py.
"""

import argparse
import copy
import fcntl
import hashlib
import json
import os
import subprocess
import traceback
from pathlib import Path

import cv2
import numpy as np
import torch
from evaluate import (
    CFG,
    OUT,
    Deformation,
    GaussianModel,
    ModelParams,
    PipelineParams,
    Rotation,
    Scene,
    Slerp,
    getWorld2View2,
    metrics,
    panel,
    render_fastgs,
    save_rgb,
)
from utils.loss_utils import ssim


def write_json(path, data):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, indent=2, allow_nan=False))
    temp.replace(path)


def gpu_exclusive():
    result = subprocess.check_output(
        ["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits"], text=True
    )
    others = {int(x) for x in result.split() if x.isdigit()} - {os.getpid()}
    if others:
        raise RuntimeError(f"GPU contention: {others}; stopping only this job")


def load_pc(path):
    pc = GaussianModel(3)
    pc.load_ply(str(path))
    return pc


def free_camera(a, b):
    c = copy.copy(a)
    c.R = Slerp([0, 1], Rotation.from_matrix(np.stack([a.R, b.R])))(0.5).as_matrix()
    c.T = (a.T + b.T) / 2
    c.world_view_transform = (
        torch.tensor(getWorld2View2(c.R, c.T, c.trans, c.scale)).transpose(0, 1).cuda()
    )
    c.full_proj_transform = c.world_view_transform @ c.projection_matrix
    c.camera_center = c.world_view_transform.inverse()[3, :3]
    return c


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=OUT.parent / "steven-person-4d-reference-20261014"
    )
    args = parser.parse_args()
    dest = args.output.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    lock = (OUT / "gpu.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    torch.set_num_threads(4)
    try:
        gpu_exclusive()
        with torch.no_grad():
            run(dest)
    except BaseException:
        with (dest / "failures.log").open("a") as f:
            traceback.print_exc(file=f)
        raise
    finally:
        lock.close()


def run(dest):
    p = argparse.ArgumentParser()
    mp, pp = ModelParams(p), PipelineParams(p)
    a = p.parse_args(["-s", CFG["source_path"], "-m", str(OUT / "dynamic"), "--resolution", "2"])
    a.masks = ""  # Preserve unmodified RGB targets; masks enter foreground metrics separately.
    a.data_sources = "original"
    g = GaussianModel(3)
    scene = Scene(mp.extract(a), g, load_iteration=15000, shuffle=False)
    pipe = pp.extract(a)
    cameras = sorted(scene.getTrainCameras(), key=lambda c: c.image_name)
    assert len(cameras) == 93 and cameras[0].image_name == "frame_000000"
    g.deformation = Deformation.load(OUT / "dynamic", 15000).eval()
    assert g.deformation.times[cameras[0].image_name] == 0
    black = torch.zeros(3, device="cuda")
    targets, masks = [], []
    for cam in cameras:
        assert cam.original_image.dtype == torch.uint8
        targets.append(cam.original_image[:3].cuda().float() / 255)
        mask = cv2.imread(str(Path(CFG["masks"]) / (cam.image_name + ".png")), 0)
        mask = (
            cv2.resize(mask, (cam.image_width, cam.image_height), interpolation=cv2.INTER_NEAREST)
            > 127
        )
        masks.append(torch.as_tensor(mask, device="cuda"))
    inputs = [
        Path(CFG["source_path"]) / "images/frame_000000.png",
        OUT / "dynamic/point_cloud/iteration_15000/point_cloud.ply",
        OUT / "dynamic/deform/iteration_15000/deform.pth",
        OUT / "static30k/point_cloud/iteration_30000/point_cloud.ply",
        OUT / "person_frozen_t050.ply",
    ]
    provenance = dict(
        inputs={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs},
        source_path=CFG["source_path"],
        masks=CFG["masks"],
        objective="maximize reference foreground PSNR; ties choose smaller t",
        mean_objective="maximize arithmetic mean foreground PSNR across all 93 cameras at same t",
        resolution=[cameras[0].image_width, cameras[0].image_height],
        background="black for metrics; white alpha composite for panels",
        ssim="FastGS Gaussian 11x11 sigma=1.5, full RGB image, zero padding",
        time_map=g.deformation.times,
        mult=0.5,
    )
    if (dest / "provenance.json").exists():
        assert json.loads((dest / "provenance.json").read_text()) == provenance, (
            "Resume inputs changed"
        )
    write_json(dest / "provenance.json", provenance)

    def quality(rgb, i):
        diff = rgb - targets[i]
        mse = diff.square()
        return dict(
            psnr=float(-10 * torch.log10(mse.mean())),
            l1=float(diff.abs().mean()),
            ssim=float(ssim(rgb, targets[i])),
            foreground_psnr=float(-10 * torch.log10(mse[:, masks[i]].mean())),
            foreground_l1=float(diff.abs()[:, masks[i]].mean()),
        )

    batches = dest / "batches"
    batches.mkdir(exist_ok=True)
    rows = {}

    def scan(times):
        for t in sorted(set(round(float(t), 6) for t in times)):
            if t in rows:
                continue
            gpu_exclusive()
            path = batches / f"t_{t:.6f}.json"
            if path.exists():
                row = json.loads(path.read_text())
                assert len(row["views"]) == 93 and row["time"] == t
            else:
                attrs = g.deformation.attributes(g, t)
                stats = []
                for i, cam in enumerate(cameras):
                    cam.time = t
                    result = render_fastgs(cam, g, pipe, black, 0.5, precomputed_deformation=attrs)
                    stats.append(
                        dict(view=cam.image_name, **quality(result["render"].clamp(0, 1), i))
                    )
                    if (i + 1) % 10 == 0:
                        gpu_exclusive()
                        write_json(batches / "partial.json", dict(time=t, views=stats))
                row = dict(
                    time=t,
                    reference=stats[0],
                    mean={
                        k: float(np.mean([s[k] for s in stats])) for k in stats[0] if k != "view"
                    },
                    views=stats,
                )
                write_json(path, row)
                print(
                    f"t={t:.6f} front={row['reference']['foreground_psnr']:.4f} mean93={row['mean']['foreground_psnr']:.4f}",
                    flush=True,
                )
            rows[t] = row
            write_json(dest / "curve.json", sorted(rows.values(), key=lambda r: r["time"]))

    def best(which):
        return max(sorted(rows), key=lambda t: rows[t][which]["foreground_psnr"])

    scan(np.linspace(0, 1, 51))
    coarse = sorted(rows)
    for radius, step in [(0.02, 0.002), (0.002, 0.0002)]:
        centers = [best("reference"), best("mean")]
        scan(
            [
                t
                for center in centers
                for t in np.arange(
                    max(0, center - radius), min(1, center + radius) + step / 2, step
                )
                if 0 <= t <= 1
            ]
        )
    tstar, tmean = best("reference"), best("mean")
    selection = dict(
        reference_time=tstar,
        mean93_time=tmean,
        difference=abs(tstar - tmean),
        reference=rows[tstar]["reference"],
        mean93=rows[tmean]["mean"],
        sampled_times=len(rows),
        coarse_times=coarse,
        refinement_steps=[0.002, 0.0002],
        alternative_optima={
            scope: {
                k: (max if k in ["psnr", "ssim", "foreground_psnr"] else min)(
                    sorted(rows), key=lambda t: rows[t][scope][k]
                )
                for k in ["psnr", "l1", "ssim", "foreground_psnr", "foreground_l1"]
            }
            for scope in ["reference", "mean"]
        },
    )
    write_json(dest / "selection.json", selection)
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 3, figsize=(16, 8))
    ordered = sorted(rows)
    for j, scope in enumerate(["reference", "mean"]):
        for ax, metric in zip(axes[j], ["foreground_psnr", "l1", "ssim"], strict=True):
            ax.plot(ordered, [rows[t][scope][metric] for t in ordered], ".-", markersize=3)
            ax.axvline(tstar, color="green", linestyle="--", label=f"reference t={tstar:.4f}")
            ax.axvline(tmean, color="orange", linestyle=":", label=f"mean93 t={tmean:.4f}")
            ax.set(xlabel="Frozen time", ylabel=metric, title=scope)
            ax.grid(alpha=0.25)
    axes[0, 0].legend()
    fig.tight_layout()
    fig.savefig(dest / "time_curve.png", dpi=160)
    plt.close(fig)

    frozen_path = dest / "person_frozen_reference.ply"
    frozen = load_pc(inputs[1])
    xyz, scales, rotation, _ = g.deformation.attributes(g, tstar)
    frozen._xyz = torch.nn.Parameter(xyz)
    frozen._scaling = torch.nn.Parameter(scales.clamp_min(1e-12).log())
    frozen._rotation = torch.nn.Parameter(rotation)
    frozen.save_ply(str(frozen_path))
    del frozen
    frozen = load_pc(frozen_path)
    free = free_camera(cameras[0], cameras[23])
    checks = []
    for cam in [cameras[0], cameras[23], cameras[46], free]:
        cam.time = tstar
        dynamic = render_fastgs(cam, g, pipe, black, 0.5)["render"]
        static = render_fastgs(cam, frozen, pipe, black, 0.5)["render"]
        checks.append(
            dict(
                max_abs=float((dynamic - static).abs().max()),
                mean_abs=float((dynamic - static).abs().mean()),
            )
        )
    write_json(dest / "freeze_validation.json", dict(time=tstar, checks=checks))
    assert max(c["max_abs"] for c in checks) < 1 / 255 and max(c["mean_abs"] for c in checks) < 1e-6
    del frozen
    g = None
    torch.cuda.empty_cache()
    allstats, images = {}, {}
    models = {"static30k": inputs[3], "frozen_t050": inputs[4], "frozen_reference": frozen_path}
    for name, path in models.items():
        gpu_exclusive()
        pc = load_pc(path)
        folder = dest / name
        folder.mkdir(exist_ok=True)
        stats = []
        for i, cam in enumerate(cameras):
            if i % 10 == 0:
                gpu_exclusive()
            result = render_fastgs(cam, pc, pipe, black, 0.5, render_invdepth=True)
            rgb = result["render"].clamp(0, 1)
            rgb_np = rgb.permute(1, 2, 0).cpu().numpy()
            alpha = result["accum_alpha"].squeeze().cpu().numpy()
            stats.append(
                dict(
                    **metrics(rgb_np, alpha, cam),
                    **{k: v for k, v in quality(rgb, i).items() if k != "foreground_psnr"},
                )
            )
            if i in [0, 23, 46]:
                white = rgb_np + 1 - alpha[..., None]
                images[name, i] = white
                save_rgb(folder / f"{cam.image_name}_white.png", white)
                save_rgb(folder / f"{cam.image_name}_black.png", rgb_np)
                np.save(folder / f"{cam.image_name}_alpha.npy", alpha)
            write_json(
                folder / "evaluation.json",
                dict(
                    views=stats,
                    mean={
                        k: float(np.mean([s[k] for s in stats])) for k in stats[0] if k != "view"
                    },
                ),
            )
        result = render_fastgs(free, pc, pipe, black, 0.5, render_invdepth=True)
        rgb = result["render"].clamp(0, 1).permute(1, 2, 0).cpu().numpy()
        alpha = result["accum_alpha"].squeeze().cpu().numpy()
        images[name, "free"] = rgb + 1 - alpha[..., None]
        save_rgb(folder / "free_white.png", images[name, "free"])
        allstats[name] = json.loads((folder / "evaluation.json").read_text())
        del pc
        torch.cuda.empty_cache()
    labels = ["Static 30k (masked)", "4D frozen t=0.5000", f"4D reference t={tstar:.4f}"]
    for key, title in [(0, "front"), (23, "side"), (46, "back"), ("free", "free")]:
        panel([images[n, key] for n in models], labels, dest / f"compare_{title}.png")
        panel([images[n, key][:360, 120:540] for n in models], labels, dest / f"head_{title}.png")
    write_json(dest / "comparison.json", allstats)
    write_json(
        dest / "completed.json",
        dict(
            time=tstar,
            mean_time=tmean,
            ply=str(frozen_path),
            free_camera="midpoint SE(3) interpolation frame_000000 to frame_000023; no GT metric",
        ),
    )
    print(json.dumps(selection, indent=2), flush=True)


if __name__ == "__main__":
    main()
