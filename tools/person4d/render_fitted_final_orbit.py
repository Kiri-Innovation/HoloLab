"""Render the approved t*=0 PLY from a constant-radius fitted camera orbit.

The source cameras define the orbit centre, vertical axis, framing, and starting
heading.  This script never loads the deformation network or applies restoration.
"""

import argparse
import copy
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
from select_time import gpu_exclusive
from utils.graphics_utils import getWorld2View2


def normalize(vector):
    return vector / np.linalg.norm(vector)


def fitted_cameras(cameras, target, radius):
    """Make one level, equally-spaced orbit while retaining the legacy first heading."""
    centers = np.array([camera.camera_center.cpu().numpy() for camera in cameras])
    # The least-varying PCA direction of the legacy camera centres is the orbit axis.
    _u, _s, right_vectors = np.linalg.svd(centers - centers.mean(axis=0), full_matrices=False)
    axis = right_vectors[-1]
    first_vector = centers[0] - target
    first_height = np.dot(first_vector, axis)
    first_radial = normalize(first_vector - first_height * axis)
    second_radial = normalize(np.cross(axis, first_radial))
    # Keep frame ordering moving in the same direction as the legacy capture.
    next_radial = centers[1] - target - np.dot(centers[1] - target, axis) * axis
    if np.dot(next_radial, second_radial) < 0:
        second_radial *= -1
    height = float(np.mean((centers - target) @ axis))
    result = []
    for index, source in enumerate(cameras):
        angle = 2 * np.pi * index / len(cameras)
        position = (
            target
            + height * axis
            + radius * (np.cos(angle) * first_radial + np.sin(angle) * second_radial)
        )
        forward = normalize(target - position)
        down = normalize(axis - np.dot(axis, forward) * forward)
        right = normalize(np.cross(down, forward))
        rotation = np.column_stack((right, down, forward))
        camera = copy.copy(source)
        camera.R = rotation
        camera.T = -rotation.T @ position
        camera.world_view_transform = (
            torch.tensor(getWorld2View2(camera.R, camera.T, camera.trans, camera.scale))
            .transpose(0, 1)
            .cuda()
        )
        camera.full_proj_transform = camera.world_view_transform @ camera.projection_matrix
        camera.camera_center = camera.world_view_transform.inverse()[3, :3]
        result.append(camera)
    return result, axis, height


def alpha_bbox(alpha, cutoff=0.50):
    ys, xs = np.where(alpha > cutoff)
    assert len(xs), "Render has no foreground"
    return [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]


def main():
    destination = OUT.parent / "steven-person-4d-reference-20261014"
    ply = destination / "person_frozen_reference.ply"
    source_provenance = json.loads((destination / "final_orbit_provenance.json").read_text())
    assert json.loads((destination / "completed.json").read_text())["time"] == 0
    assert source_provenance["gaussians"] == 175412
    target = np.asarray(source_provenance["camera_target_least_squares"])
    old_radius = source_provenance["radius_scene_units"]["max"]
    # At 15%, 30%, and 70% above the legacy maximum, the reconstructed front
    # arm still touched the frame.  This fixed 130% margin is the first
    # conservative tested framing which clears every opaque alpha bbox.
    radius = old_radius * 2.30
    frame_dir = destination / "frozen_reference/renders_fitted"
    frame_dir.mkdir(exist_ok=True)
    digest = hashlib.sha256(ply.read_bytes()).hexdigest()
    with (OUT / "gpu.lock").open("a") as lock, torch.no_grad():
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        gpu_exclusive()
        parser = argparse.ArgumentParser()
        model_params, pipeline_params = ModelParams(parser), PipelineParams(parser)
        args = parser.parse_args(
            ["-s", CFG["source_path"], "-m", str(OUT / "dynamic"), "--resolution", "2"]
        )
        args.masks = ""
        args.data_sources = "original"
        model = GaussianModel(3)
        scene = Scene(model_params.extract(args), model, load_iteration=15000, shuffle=False)
        model.load_ply(str(ply))
        assert len(model.get_xyz) == 175412
        legacy = sorted(scene.getTrainCameras(), key=lambda camera: camera.image_name)
        assert len(legacy) == 93
        cameras, axis, height = fitted_cameras(legacy, target, radius)
        black = torch.zeros(3, device="cuda")
        checks = []
        for index, camera in enumerate(cameras):
            gpu_exclusive()
            result = render_fastgs(
                camera, model, pipeline_params.extract(args), black, 0.5, render_invdepth=True
            )
            rgb = result["render"].clamp(0, 1).permute(1, 2, 0).cpu().numpy()
            alpha = result["accum_alpha"].squeeze().cpu().numpy()
            bbox = alpha_bbox(alpha)
            # Use opaque foreground for framing.  The PLY has sparse, translucent
            # trailing Gaussians outside the reconstructed person.
            margin = min(bbox[0], bbox[1], 649 - bbox[2], 649 - bbox[3])
            checks.append(
                dict(
                    frame=index,
                    bbox_alpha_gt_050=bbox,
                    margin_px=int(margin),
                    bbox_width_fraction=(bbox[2] - bbox[0] + 1) / 650,
                    bbox_height_fraction=(bbox[3] - bbox[1] + 1) / 650,
                )
            )
            save_rgb(frame_dir / f"{index:03d}.png", rgb + 1 - alpha[..., None])
            if index % 23 == 0:
                print(f"Rendered {index + 1}/93", flush=True)
        assert min(check["margin_px"] for check in checks) >= 10, checks
        assert hashlib.sha256(ply.read_bytes()).hexdigest() == digest
    output = destination / "final_orbit_fitted.mp4"
    command = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-framerate",
        "15",
        "-i",
        str(frame_dir / "%03d.png"),
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
        str(output),
    ]
    subprocess.run(command, check=True)
    fractions = np.array([[c["bbox_width_fraction"], c["bbox_height_fraction"]] for c in checks])
    provenance = dict(
        ply=str(ply),
        ply_sha256=digest,
        frozen_time=0,
        gaussians=175412,
        frame_count=93,
        fps=15,
        duration_seconds=93 / 15,
        resolution=[650, 650],
        camera_path="constant-radius, equally-spaced fitted orbit derived from legacy cameras",
        camera_target=target.tolist(),
        axis=axis.tolist(),
        axial_offset=height,
        radius_scene_units=radius,
        radius_margin_over_old_max=1.30,
        gaussian_scale_modifier=0.5,
        sh_degree=3,
        background="black render, white composite: rgb + (1 - alpha)",
        bounding_box_fraction=dict(
            width_min=float(fractions[:, 0].min()),
            width_max=float(fractions[:, 0].max()),
            height_min=float(fractions[:, 1].min()),
            height_max=float(fractions[:, 1].max()),
        ),
        min_alpha_bbox_margin_px=min(c["margin_px"] for c in checks),
        frame_checks=checks,
        encoding_command=command,
    )
    (destination / "final_orbit_fitted_provenance.json").write_text(
        json.dumps(provenance, indent=2) + "\n"
    )
    print(json.dumps({k: v for k, v in provenance.items() if k != "frame_checks"}, indent=2))


if __name__ == "__main__":
    main()
