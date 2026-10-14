"""Render identical cameras/backgrounds, audit export, and compare fixed-time models.
All PNG panels are Gaussian renders. GT images enter numeric diagnostics only.
"""

import argparse
import copy
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image, ImageDraw, ImageFont
from run_experiment import CFG, OLD, OUT

sys.path.insert(0, "/cloud/cloud-ssd1/FastGS-Pano")
from arguments import ModelParams, PipelineParams
from dynamic.deformation import Deformation
from gaussian_renderer import render_fastgs
from scene import Scene
from scene.gaussian_model import GaussianModel
from scipy.spatial.transform import Rotation, Slerp
from utils.graphics_utils import getWorld2View2

p = argparse.ArgumentParser()
mp = ModelParams(p)
pp = PipelineParams(p)
a = p.parse_args(["-s", CFG["source_path"], "-m", str(OUT / "dynamic"), "--resolution", "2"])
a.masks = ""
g = GaussianModel(3)
scene = Scene(mp.extract(a), g, load_iteration=15000, shuffle=False)
pipe = pp.extract(a)
cameras = sorted(scene.getTrainCameras(), key=lambda c: c.image_name)
font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 19)


def save_rgb(path, rgb):
    Image.fromarray((rgb.clip(0, 1) * 255).round().astype("uint8")).save(path)


def panel(images, labels, path, crop=False):
    h, w = images[0].shape[:2]
    canvas = Image.new("RGB", (w * len(images), h + 36), (22, 29, 38))
    d = ImageDraw.Draw(canvas)
    for j, (im, label) in enumerate(zip(images, labels, strict=False)):
        canvas.paste(Image.fromarray((im.clip(0, 1) * 255).round().astype("uint8")), (j * w, 36))
        d.text((j * w + 10, 8), label, font=font, fill="white")
    canvas.save(path)
    return np.array(canvas)


def metrics(rgb, alpha, cam):
    gt = cam.original_image[:3].permute(1, 2, 0).cpu().numpy() / 255.0
    mask = cv2.imread(str(Path(CFG["masks"]) / (cam.image_name + ".png")), 0)
    mask = cv2.resize(mask, (alpha.shape[1], alpha.shape[0]), interpolation=cv2.INTER_NEAREST) > 127
    interior = cv2.erode(mask.astype("uint8"), np.ones((7, 7), np.uint8)).astype(bool)
    dil = cv2.dilate(mask.astype("uint8"), np.ones((11, 11), np.uint8)).astype(bool)
    bg = ~dil
    ys = np.where(mask)[0]
    head = np.indices(mask.shape)[0] < ys.min() + 0.55 * (ys.max() - ys.min())
    ring = cv2.dilate(mask.astype("uint8"), np.ones((31, 31), np.uint8)).astype(bool) & ~dil & head
    edge = (
        cv2.dilate(mask.astype("uint8"), np.ones((3, 3), np.uint8))
        - cv2.erode(mask.astype("uint8"), np.ones((3, 3), np.uint8))
    ).astype(bool)
    band = cv2.dilate(edge.astype("uint8"), np.ones((21, 21), np.uint8)).astype(bool) & head
    gray = cv2.cvtColor(rgb.astype("float32"), cv2.COLOR_RGB2GRAY)
    lap = cv2.Laplacian(gray, cv2.CV_32F)
    own = (alpha > 0.5).astype("uint8")
    own_ys = np.where(own)[0]
    cut = own_ys.min() + 0.55 * (own_ys.max() - own_ys.min())
    own_head = np.indices(own.shape)[0] < cut
    contours, _ = cv2.findContours(own, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    perimeter = 0.0
    for contour in contours:
        xy = contour[:, 0, :].astype(float)
        nxt = np.roll(xy, -1, axis=0)
        selected = (xy[:, 1] < cut) & (nxt[:, 1] < cut)
        perimeter += np.linalg.norm(nxt - xy, axis=1)[selected].sum()
    assert perimeter > 0
    return dict(
        own_head_soft_width_px=float(
            (((alpha > 0.05) & (alpha < 0.95)) & own_head).sum() / perimeter
        ),
        own_head_outer_opacity_px=float(alpha[(alpha < 0.5) & own_head].sum() / perimeter),
        view=cam.image_name,
        foreground_psnr=float(-10 * np.log10(((rgb - gt) ** 2)[mask].mean())),
        head_psnr=float(-10 * np.log10(((rgb - gt) ** 2)[interior & head].mean())),
        background_alpha=float(alpha[bg].mean()),
        head_outer_alpha=float(alpha[ring].mean()),
        head_band_soft_fraction=float(((alpha > 0.05) & (alpha < 0.95))[band].mean()),
        head_laplacian_variance=float(lap[interior & head].var()),
        silhouette_iou=float(((alpha > 0.5) & mask).sum() / ((alpha > 0.5) | mask).sum()),
    )


models = {
    "old_static": OLD / "person_clean.ply",
    "static30k": OUT / "static30k/point_cloud/iteration_30000/point_cloud.ply",
    "frozen_t050": OUT / "person_frozen_t050.ply",
    "dynamic_matched": OUT / "dynamic/point_cloud/iteration_15000/point_cloud.ply",
}
allstats = {}
selected = {}
free = {}
black = torch.zeros(3, device="cuda")
with torch.no_grad():
    for name, path in models.items():
        pc = GaussianModel(3)
        pc.load_ply(str(path))
        folder = OUT / name / "renders"
        folder.mkdir(parents=True, exist_ok=True)
        if name == "dynamic_matched":
            pc.deformation = Deformation.load(OUT / "dynamic", 15000)
        stats = []
        for i, cam in enumerate(cameras):
            if name == "dynamic_matched":
                cam.time = pc.deformation.times[cam.image_name]
            result = render_fastgs(cam, pc, pipe, black, 0.5, render_invdepth=True)
            rgb = result["render"].clamp(0, 1).permute(1, 2, 0).cpu().numpy()
            alpha = result["accum_alpha"].squeeze().cpu().numpy()
            white = rgb + 1 - alpha[..., None]
            stats.append(metrics(rgb, alpha, cam))
            save_rgb(folder / f"{i:03d}.png", white)
            if i in [0, 23, 46]:
                selected[name, i] = white
            (OUT / name / "evaluation.json").write_text(
                json.dumps(
                    dict(
                        gaussians=len(pc.get_xyz),
                        views=stats,
                        mean={
                            k: float(np.mean([s[k] for s in stats]))
                            for k in stats[0]
                            if k != "view"
                        },
                    ),
                    indent=2,
                )
            )
        allstats[name] = json.loads((OUT / name / "evaluation.json").read_text())
        if name != "dynamic_matched":
            for i in [0, 23, 46]:
                c = copy.copy(cameras[i])
                a, b = cameras[i], cameras[i + 1]
                c.R = Slerp([0, 1], Rotation.from_matrix(np.stack([a.R, b.R])))(0.5).as_matrix()
                c.T = (a.T + b.T) / 2
                c.world_view_transform = (
                    torch.tensor(getWorld2View2(c.R, c.T, c.trans, c.scale)).transpose(0, 1).cuda()
                )
                c.full_proj_transform = (
                    c.world_view_transform.unsqueeze(0)
                    .bmm(c.projection_matrix.unsqueeze(0))
                    .squeeze(0)
                )
                c.camera_center = c.world_view_transform.inverse()[3, :3]
                r = (
                    render_fastgs(c, pc, pipe, torch.ones(3, device="cuda"), 0.5)["render"]
                    .clamp(0, 1)
                    .permute(1, 2, 0)
                    .cpu()
                    .numpy()
                )
                free[name, i] = r
                save_rgb(folder / f"free_{i:03d}.png", r)
        del pc
        torch.cuda.empty_cache()
    # Verify frozen export matches the dynamic renderer at exactly t=.5.
    pc = GaussianModel(3)
    pc.load_ply(str(models["dynamic_matched"]))
    pc.deformation = Deformation.load(OUT / "dynamic", 15000)
    frozen = GaussianModel(3)
    frozen.load_ply(str(models["frozen_t050"]))
    checks = []
    for i in [0, 23, 46]:
        cam = cameras[i]
        cam.time = 0.5
        r = render_fastgs(cam, pc, pipe, black, 0.5)["render"]
        f = render_fastgs(cam, frozen, pipe, black, 0.5)["render"]
        checks.append(
            dict(
                view=cam.image_name,
                max_abs=float((r - f).abs().max()),
                mean_abs=float((r - f).abs().mean()),
            )
        )
    xyz, scales, rot, reg = pc.deformation.attributes(pc, 0.5)
    disp = (xyz - pc.get_xyz).norm(dim=1).cpu().numpy() / pc.deformation.extent
    (OUT / "freeze_validation.json").write_text(
        json.dumps(
            dict(
                render_equivalence=checks,
                displacement_over_extent=dict(
                    median=float(np.median(disp)),
                    p95=float(np.percentile(disp, 95)),
                    max=float(disp.max()),
                ),
                time=0.5,
                regularization=float(reg),
            ),
            indent=2,
        )
    )
    # PLY scale log/exp and quaternion normalization introduce float32 roundoff.
    # Require sub-8-bit-code peak difference AND near-zero mean difference.
    assert max(c["max_abs"] for c in checks) < 1 / 255, checks
    assert max(c["mean_abs"] for c in checks) < 1e-6, checks
labels = ["Old static 15k", "Static 30k control", "4D frozen t=0.50"]
names = list(models)[:3]
for i, label in [(0, "front"), (23, "side"), (46, "back")]:
    panel([selected[n, i] for n in names], labels, OUT / f"compare_{label}.png")
    panel([free[n, i] for n in names], labels, OUT / f"compare_free_{label}.png")
    panel([selected[n, i][:360, 120:540] for n in names], labels, OUT / f"head_{label}.png")
(OUT / "comparison.json").write_text(
    json.dumps({n: v["mean"] for n, v in allstats.items()}, indent=2)
)
print((OUT / "comparison.json").read_text())
# Integration check: silhouette-only loss must train the deformation head.
cam = cameras[0]
cam.time = pc.deformation.times[cam.image_name]
result = render_fastgs(cam, pc, pipe, black, 0.5, render_invdepth=True)
alpha = result["accum_alpha"].squeeze()
mask = cv2.imread(str(Path(CFG["masks"]) / (cam.image_name + ".png")), 0)
mask = cv2.resize(mask, (alpha.shape[1], alpha.shape[0]), interpolation=cv2.INTER_NEAREST) > 127
target = torch.tensor(mask, device="cuda", dtype=torch.float32)
loss = torch.nn.functional.binary_cross_entropy(alpha.clamp(1e-5, 1 - 1e-5), target)
loss.backward()
grads = [p.grad for p in pc.deformation.parameters() if p.grad is not None]
check = dict(
    silhouette_loss=float(loss.detach()),
    deformation_gradient_l1=sum(float(g.abs().sum()) for g in grads),
    finite=all(bool(torch.isfinite(g).all()) for g in grads),
)
(OUT / "dynamic_silhouette_gradient.json").write_text(json.dumps(check, indent=2))
assert check["finite"] and check["deformation_gradient_l1"] > 0, check
