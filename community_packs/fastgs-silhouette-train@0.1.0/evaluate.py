import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, "/cloud/cloud-ssd1/FastGS-Pano")
import copy

import cv2
import numpy as np
import torch
from arguments import ModelParams, PipelineParams
from gaussian_renderer import GaussianModel, render_fastgs
from PIL import Image
from scene import Scene
from utils.graphics_utils import getProjectionMatrix

p = argparse.ArgumentParser()
p.add_argument("--source", required=True)
p.add_argument("--model", required=True)
p.add_argument("--masks", required=True)
p.add_argument("--out", required=True)
a = p.parse_args()

p = argparse.ArgumentParser()
mp = ModelParams(p)
pp = PipelineParams(p)
args = p.parse_args(["-s", a.source, "-m", a.model, "--resolution", "2"])
args.masks = ""
g = GaussianModel(3)
scene = Scene(mp.extract(args), g, load_iteration=-1, shuffle=False)
pipe = pp.extract(args)
out = Path(a.out)
out.mkdir(parents=True, exist_ok=True)
stats = []
with torch.no_grad():
    for i, cam in enumerate(scene.getTrainCameras()):
        pkg = render_fastgs(cam, g, pipe, torch.zeros(3, device="cuda"), 0.5, render_invdepth=True)
        rgb = pkg["render"].clamp(0, 1).permute(1, 2, 0).cpu().numpy()
        alpha = pkg["accum_alpha"].squeeze().cpu().numpy()
        gt = cam.original_image[:3].permute(1, 2, 0).cpu().numpy() / 255
        mask = cv2.imread(str(Path(a.masks) / (cam.image_name + ".png")), 0)
        mask = cv2.resize(mask, (rgb.shape[1], rgb.shape[0]), interpolation=cv2.INTER_NEAREST) > 127
        interior = cv2.erode(mask.astype("uint8"), np.ones((7, 7), np.uint8)).astype(bool)
        bg = ~cv2.dilate(mask.astype("uint8"), np.ones((11, 11), np.uint8)).astype(bool)
        mse = float(((rgb - gt) ** 2)[mask].mean())
        imse = float(((rgb - gt) ** 2)[interior].mean())
        stats.append(
            dict(
                view=cam.image_name,
                foreground_psnr=-10 * np.log10(mse),
                interior_psnr=-10 * np.log10(imse),
                background_alpha=float(alpha[bg].mean()),
                background_occupied=float((alpha[bg] > 0.05).mean()),
            )
        )
        (out / "frames").mkdir(exist_ok=True)
        Image.fromarray(
            ((rgb + (1 - alpha[..., None])).clip(0, 1) * 255).round().astype("uint8")
        ).save(out / "frames" / f"{i:03d}.png")
        if i in [0, 23, 46, 70, 92]:
            for name, img in [
                ("black", rgb),
                ("white", rgb + (1 - alpha[..., None])),
                ("alpha", np.repeat(alpha[..., None], 3, axis=2)),
                ("gt", gt),
            ]:
                Image.fromarray((img.clip(0, 1) * 255).round().astype("uint8")).save(
                    out / f"{i:03d}_{name}.png"
                )
        (out / "metrics.json").write_text(
            json.dumps(
                dict(
                    gaussians=len(g.get_xyz),
                    views=stats,
                    mean={
                        k: float(np.mean([r[k] for r in stats])) for k in stats[0] if k != "view"
                    },
                ),
                indent=2,
            )
        )
# Wider field of view exposes off-frame floaters, with no 2D post-mask.

with torch.no_grad():
    cam = copy.copy(scene.getTrainCameras()[0])
    cam.FoVx *= 1.35
    cam.FoVy *= 1.35
    cam.projection_matrix = (
        getProjectionMatrix(znear=cam.znear, zfar=cam.zfar, fovX=cam.FoVx, fovY=cam.FoVy)
        .transpose(0, 1)
        .cuda()
    )
    cam.full_proj_transform = (
        cam.world_view_transform.unsqueeze(0).bmm(cam.projection_matrix.unsqueeze(0)).squeeze(0)
    )
    pkg = render_fastgs(cam, g, pipe, torch.ones(3, device="cuda"), 0.5, render_invdepth=True)
    rgb = pkg["render"].clamp(0, 1).permute(1, 2, 0).cpu().numpy()
    Image.fromarray((rgb * 255).round().astype("uint8")).save(out / "wide_white.png")
print((out / "metrics.json").read_text()[-400:])
