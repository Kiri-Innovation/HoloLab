"""Render the approved t*=0 PLY with the historical 93 cameras and white composite.

Run using /cloud/cloud-ssd1/conda/envs/fastgs/bin/python with LD_LIBRARY_PATH unset.
No training, deformation, or image restoration is applied.
"""

import argparse
import fcntl
import hashlib
import json
import subprocess

import numpy as np
import torch
from evaluate import (
    CFG,
    OUT,
    GaussianModel,
    ModelParams,
    PipelineParams,
    Scene,
    render_fastgs,
    save_rgb,
)
from PIL import Image
from select_time import gpu_exclusive


def main():
    dest = OUT.parent / "steven-person-4d-reference-20261014"
    ply = dest / "person_frozen_reference.ply"
    assert json.loads((dest / "completed.json").read_text())["time"] == 0
    digest = hashlib.sha256(ply.read_bytes()).hexdigest()
    frames = dest / "frozen_reference/renders"
    frames.mkdir(exist_ok=True)
    # Share the lock used by the original experiment; never stop another job.
    with (OUT / "gpu.lock").open("a") as lock, torch.no_grad():
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        gpu_exclusive()
        torch.set_num_threads(4)
        parser = argparse.ArgumentParser()
        mp, pp = ModelParams(parser), PipelineParams(parser)
        args = parser.parse_args(
            ["-s", CFG["source_path"], "-m", str(OUT / "dynamic"), "--resolution", "2"]
        )
        args.masks = ""
        args.data_sources = "original"
        pc = GaussianModel(3)
        # Loading an existing iteration preserves scene files and reproduces legacy cameras.
        scene = Scene(mp.extract(args), pc, load_iteration=15000, shuffle=False)
        pc.load_ply(str(ply))
        assert len(pc.get_xyz) == 175412
        cameras = sorted(scene.getTrainCameras(), key=lambda c: c.image_name)
        assert len(cameras) == 93
        black = torch.zeros(3, device="cuda")
        checks, poses = [], []
        for i, cam in enumerate(cameras):
            gpu_exclusive()
            result = render_fastgs(cam, pc, pp.extract(args), black, 0.5, render_invdepth=True)
            rgb = result["render"].clamp(0, 1).permute(1, 2, 0).cpu().numpy()
            alpha = result["accum_alpha"].squeeze().cpu().numpy()
            target = frames / f"{i:03d}.png"
            save_rgb(target, rgb + 1 - alpha[..., None])
            ys, xs = np.where(alpha > 0.05)
            border = np.concatenate([alpha[0], alpha[-1], alpha[:, 0], alpha[:, -1]])
            check = dict(
                frame=i,
                bbox_alpha_gt_005=[int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())],
                border_alpha_max=float(border.max()),
            )
            if i in (0, 23, 46):
                expected = dest / "frozen_reference" / f"{cam.image_name}_white.png"
                error = np.abs(
                    np.asarray(Image.open(target)).astype(int)
                    - np.asarray(Image.open(expected)).astype(int)
                )
                check["existing_final_reference_max_pixel_error"] = int(error.max())
                assert error.max() == 0, check
            checks.append(check)
            poses.append(
                dict(
                    frame=i,
                    name=cam.image_name,
                    R=cam.R.tolist(),
                    T=cam.T.tolist(),
                    center=cam.camera_center.cpu().tolist(),
                    fov_x=cam.FoVx,
                    fov_y=cam.FoVy,
                )
            )
            if i % 23 == 0:
                print(f"Rendered {i + 1}/93", flush=True)
        centers = np.array([p["center"] for p in poses])
        # Camera optical-axis intersection defines the orbit target without changing poses.
        directions = np.array([np.array(p["R"])[:, 2] for p in poses])
        projectors = np.eye(3)[None] - directions[:, :, None] * directions[:, None, :]
        center = np.linalg.lstsq(
            projectors.sum(0), np.einsum("nij,nj->i", projectors, centers), rcond=None
        )[0]
        radii = np.linalg.norm(centers - center, axis=1)
        report = dict(
            ply=str(ply),
            ply_sha256=digest,
            frozen_time=0,
            gaussians=175412,
            frame_count=93,
            fps=15,
            duration_seconds=93 / 15,
            resolution=[cam.image_width, cam.image_height],
            camera_path="Legacy sorted original COLMAP training cameras, unchanged",
            camera_target_least_squares=center.tolist(),
            radius_scene_units=dict(
                min=float(radii.min()), max=float(radii.max()), mean=float(radii.mean())
            ),
            gaussian_scale_modifier=0.5,
            sh_degree=3,
            background="black render, white composite: rgb + (1 - alpha)",
            cameras=poses,
            frame_checks=checks,
        )
        assert hashlib.sha256(ply.read_bytes()).hexdigest() == digest
    command = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-framerate",
        "15",
        "-i",
        str(frames / "%03d.png"),
        "-frames:v",
        "93",
        "-an",
        "-c:v",
        "libx264",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(dest / "final_orbit.mp4"),
    ]
    subprocess.run(command, check=True)
    report["encoding_command"] = command
    (dest / "final_orbit_provenance.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in ("cameras", "frame_checks")}, indent=2
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
