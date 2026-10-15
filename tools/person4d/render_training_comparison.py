"""Render a static FastGS checkpoint against its original extracted video frames.

Run with the FastGS Python environment and LD_LIBRARY_PATH unset. This reads
existing model/scene files only. SIMPLE_RADIAL source cameras are supported;
native pinhole renders are also retained before mapping to video coordinates.
"""

import argparse
import ast
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image

sys.path.insert(0, "/cloud/cloud-ssd1/FastGS-Pano")
from arguments import ModelParams, PipelineParams
from gaussian_renderer import render_fastgs
from scene import Scene
from scene.colmap_loader import Camera as ColmapCamera
from scene.colmap_loader import read_extrinsics_text
from scene.gaussian_model import GaussianModel


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def gpu_others():
    result = subprocess.check_output(
        ["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits"], text=True
    )
    return {int(p) for p in result.split() if p.isdigit()} - {os.getpid()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("model", "frames", "raw-cameras", "video", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--fps", type=int, default=15)
    opts = parser.parse_args()
    out = opts.output
    out.mkdir(parents=True, exist_ok=True)
    # Parse Namespace data without executing checkpoint configuration code.
    tree = ast.parse((opts.model / "cfg_args").read_text(), mode="eval").body
    assert (
        isinstance(tree, ast.Call)
        and isinstance(tree.func, ast.Name)
        and tree.func.id == "Namespace"
    )
    cfg = {item.arg: ast.literal_eval(item.value) for item in tree.keywords}
    assert not cfg.get("dynamic") and not cfg.get("eval")
    originals = sorted(opts.frames.glob("*.png"))
    names = [p.stem for p in originals]
    stored = {c["img_name"]: c for c in json.loads((opts.model / "cameras.json").read_text())}
    assert set(names) == set(stored), "Extracted frames and training cameras differ"
    raw_cams = {}
    for line in (opts.raw_cameras / "cameras.txt").read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = line.split()
        cid, model, width, height = fields[:4]
        raw_cams[int(cid)] = ColmapCamera(
            int(cid), model, int(width), int(height), np.array([float(v) for v in fields[4:]])
        )
    raw_images = {
        Path(i.name).stem: i
        for i in read_extrinsics_text(str(opts.raw_cameras / "images.txt")).values()
    }
    assert set(names) == set(raw_images)
    iteration = max(
        int(p.name.split("_")[-1]) for p in (opts.model / "point_cloud").glob("iteration_*")
    )
    ply = opts.model / "point_cloud" / f"iteration_{iteration}" / "point_cloud.ply"
    digest = sha(ply)
    raw_size = Image.open(originals[0]).size
    native_size = (stored[names[0]]["width"], stored[names[0]]["height"])
    size = tuple((max(a, b) + 1) // 2 * 2 for a, b in zip(raw_size, native_size, strict=True))
    for sub in ("renders", "renders_training_camera", "checks"):
        (out / sub).mkdir(exist_ok=True)
    # Independently decode the source to verify extraction starts at frame zero
    # and retains every frame in order, not an old/strided input sequence.
    decode = subprocess.Popen(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(opts.video),
            "-frames:v",
            str(len(originals)),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        stdout=subprocess.PIPE,
    )
    for path in originals:
        data = decode.stdout.read(raw_size[0] * raw_size[1] * 3)
        assert data == Image.open(path).convert("RGB").tobytes(), f"Video mismatch: {path.name}"
    assert decode.wait() == 0
    print(f"Verified {len(originals)} extracted frames against source video", flush=True)
    p = argparse.ArgumentParser()
    mp, pp = ModelParams(p), PipelineParams(p)
    args = p.parse_args([])
    vars(args).update(cfg)
    args.model_path = str(opts.model)
    args.resolution = 1  # Preserve training intrinsics/FOV at full image resolution.
    args.data_device = "cpu"
    args.frame_gpu_budget = 1
    poses = []
    with (out / "gpu.lock").open("a") as lock, torch.no_grad():
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        deadline = time.monotonic() + 900
        while gpu_others():
            assert time.monotonic() < deadline, "GPU did not become exclusive"
            print(f"Waiting for GPU processes to exit naturally: {gpu_others()}", flush=True)
            time.sleep(15)
        print("GPU exclusive; loading checkpoint", flush=True)
        torch.set_num_threads(4)
        gaussians = GaussianModel(cfg["sh_degree"])
        scene = Scene(mp.extract(args), gaussians, load_iteration=iteration, shuffle=False)
        cams = sorted(scene.getTrainCameras(), key=lambda c: c.image_name)
        assert [c.image_name for c in cams] == names
        bg = torch.tensor(
            [1.0, 1.0, 1.0] if cfg["white_background"] else [0.0, 0.0, 0.0], device="cuda"
        )
        # Build inverse lens map once per camera calibration (raw pixel -> pinhole).
        maps = {}
        command = [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{size[0] * 2 + 2}x{size[1]}",
            "-r",
            str(opts.fps),
            "-i",
            "-",
            "-an",
            "-c:v",
            "libx264",
            "-threads",
            "4",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(out / "original_vs_render.mp4"),
        ]
        # Finish GPU work in one short batch before CPU PNG compression/encoding.
        # This minimizes the exclusive GPU window on the shared workstation.
        native_rgbs = []
        for cam in cams:
            assert not gpu_others(), "Another GPU process appeared; stopping our render"
            rendered = render_fastgs(cam, gaussians, pp.extract(args), bg, 0.5)["render"]
            native_rgbs.append(
                (rendered.clamp(0, 1).permute(1, 2, 0).cpu().numpy() * 255).round().astype(np.uint8)
            )
        assert not gpu_others(), "Another GPU process appeared during rendering"
        gaussian_count = len(gaussians.get_xyz)
        del rendered, gaussians, scene, bg
        torch.cuda.empty_cache()
        print("All GPU renders complete; saving PNGs and encoding on CPU", flush=True)
        encoder = subprocess.Popen(command, stdin=subprocess.PIPE)
        try:
            for index, (cam, original) in enumerate(zip(cams, originals, strict=True)):
                saved = stored[cam.image_name]
                raw = raw_images[cam.image_name]
                np.testing.assert_allclose(cam.R, raw.qvec2rotmat().T, atol=1e-7)
                np.testing.assert_allclose(cam.T, raw.tvec, atol=1e-7)
                np.testing.assert_allclose(cam.R, saved["rotation"], atol=1e-7)
                np.testing.assert_allclose(-cam.R @ cam.T, saved["position"], atol=1e-7)
                fx = cam.image_width / (2 * np.tan(cam.FoVx / 2))
                fy = cam.image_height / (2 * np.tan(cam.FoVy / 2))
                np.testing.assert_allclose([fx, fy], [saved["fx"], saved["fy"]], rtol=1e-7)
                rgb = native_rgbs[index]
                native = out / "renders_training_camera" / original.name
                Image.fromarray(rgb).save(native)
                key = raw.camera_id
                if key not in maps:
                    c = raw_cams[key]
                    assert c.model == "SIMPLE_RADIAL" and (c.width, c.height) == raw_size
                    f, cx, cy, k = c.params
                    y, x = np.indices((raw_size[1], raw_size[0]), dtype=np.float32)
                    distorted = np.stack([(x - cx) / f, (y - cy) / f], -1)
                    uv = distorted.copy()
                    for _ in range(30):
                        radial = 1 + k * (uv**2).sum(axis=-1, keepdims=True)
                        uv = distorted / radial
                    residual = uv * (1 + k * (uv**2).sum(axis=-1, keepdims=True)) - distorted
                    assert np.abs(residual).max() < 1e-6
                    maps[key] = (
                        (uv[..., 0] * fx + cam.image_width / 2).astype(np.float32),
                        (uv[..., 1] * fy + cam.image_height / 2).astype(np.float32),
                    )
                aligned = cv2.remap(rgb, *maps[key], cv2.INTER_LINEAR)
                right = Image.fromarray(aligned).resize(size, Image.Resampling.LANCZOS)
                target = out / "renders" / original.name
                right.save(target)
                left = Image.open(original).convert("RGB").resize(size, Image.Resampling.LANCZOS)
                panel = Image.new("RGB", (size[0] * 2 + 2, size[1]), (200, 200, 200))
                panel.paste(left, (0, 0))
                panel.paste(right, (size[0] + 2, 0))
                encoder.stdin.write(panel.tobytes())
                if index in (0, len(cams) // 2, len(cams) - 1):
                    panel.save(out / "checks" / original.name)
                poses.append(
                    dict(
                        index=index,
                        name=cam.image_name,
                        R=cam.R.tolist(),
                        T=cam.T.tolist(),
                        fov_x=cam.FoVx,
                        fov_y=cam.FoVy,
                        original_sha256=sha(original),
                        native_render_sha256=sha(native),
                        aligned_render_sha256=sha(target),
                    )
                )
                if index % 15 == 0:
                    print(f"Rendered {index + 1}/{len(cams)}", flush=True)
        finally:
            encoder.stdin.close()
        assert encoder.wait() == 0
        assert sha(ply) == digest
    report = dict(
        model=str(opts.model),
        model_ply_sha256=digest,
        iteration=iteration,
        gaussians=gaussian_count,
        source_path=cfg["source_path"],
        masks=cfg["masks"],
        source_video=str(opts.video),
        extracted_frames=str(opts.frames),
        frame_count=len(poses),
        fps=opts.fps,
        duration_seconds=len(poses) / opts.fps,
        panel_resolution=list(size),
        video_resolution=[size[0] * 2 + 2, size[1]],
        native_render_resolution=list(native_size),
        background="white" if cfg["white_background"] else "black",
        rasterizer_mult=0.5,
        alignment="Original extracted RGB on left; training pinhole render reprojected through original SIMPLE_RADIAL lens on right; both resized to common even size.",
        checks="Every extracted frame equals source-video decode; all poses and intrinsics match checkpoint cameras.json; raw COLMAP extrinsics match training extrinsics.",
        frames=poses,
        encoding_command=command,
    )
    (out / "provenance.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in ("frames", "encoding_command")}, indent=2
        )
    )


if __name__ == "__main__":
    main()
