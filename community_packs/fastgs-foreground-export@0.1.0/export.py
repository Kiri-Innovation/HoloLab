"""Export Gaussian centers observed inside at least one foreground silhouette."""

import argparse
import json
import shutil
import sys
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--fastgs-root", default="/cloud/cloud-ssd1/FastGS-Pano")
    p.add_argument("--colmap", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--masks", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--margin", type=int, default=2)
    a = p.parse_args()
    sys.path.insert(0, a.fastgs_root)
    import cv2
    import numpy as np
    import torch
    from arguments import ModelParams
    from gaussian_renderer import GaussianModel
    from scene import Scene

    p = argparse.ArgumentParser()
    mp = ModelParams(p)
    args = p.parse_args(["-s", a.colmap, "-m", a.model, "--resolution", "2"])
    g = GaussianModel(3)
    scene = Scene(mp.extract(args), g, load_iteration=-1, shuffle=False)
    with torch.no_grad():
        xyz = torch.cat([g.get_xyz, torch.ones_like(g.get_xyz[:, :1])], 1)
        seen = torch.zeros(len(xyz), device="cuda")
        outside = seen.clone()
        for cam in scene.getTrainCameras():
            mask = cv2.imread(str(Path(a.masks) / (cam.image_name + ".png")), 0)
            if mask is None:
                raise ValueError("Missing mask " + cam.image_name)
            mask = cv2.resize(
                mask, (cam.image_width, cam.image_height), interpolation=cv2.INTER_NEAREST
            )
            mask = cv2.dilate(mask, np.ones((2 * a.margin + 1, 2 * a.margin + 1), np.uint8))
            clip = xyz @ cam.full_proj_transform
            ndc = clip[:, :2] / clip[:, 3:4]
            u = ((ndc[:, 0] + 1) * cam.image_width / 2 - 0.5).round().long()
            v = ((ndc[:, 1] + 1) * cam.image_height / 2 - 0.5).round().long()
            valid = (
                (clip[:, 3] > 0)
                & (u >= 0)
                & (u < cam.image_width)
                & (v >= 0)
                & (v < cam.image_height)
            )
            fg = torch.tensor(mask > 127, device="cuda")[
                v.clamp(0, cam.image_height - 1), u.clamp(0, cam.image_width - 1)
            ]
            seen += valid
            outside += valid & ~fg
        keep = (seen >= 1) & (outside < seen)
        before = len(xyz)
        for attr in ["_xyz", "_features_dc", "_features_rest", "_opacity", "_scaling", "_rotation"]:
            setattr(
                g, attr, torch.nn.Parameter(getattr(g, attr)[keep].clone(), requires_grad=False)
            )
        out = Path(a.output)
        out.mkdir(parents=True, exist_ok=True)
        for f in ["cfg_args", "cameras.json"]:
            if (Path(a.model) / f).exists():
                shutil.copy2(Path(a.model) / f, out / f)
        g.save_ply(str(out / "point_cloud" / f"iteration_{scene.loaded_iter}" / "point_cloud.ply"))
        (out / "cleanup.json").write_text(
            json.dumps(
                dict(
                    before=before,
                    after=int(keep.sum()),
                    removed=int((~keep).sum()),
                    margin_pixels=a.margin,
                    criterion="inside at least one foreground mask",
                    views=len(scene.getTrainCameras()),
                    source=a.model,
                ),
                indent=2,
            )
        )
        print((out / "cleanup.json").read_text())


if __name__ == "__main__":
    main()
