"""Heavy-lifting worker: staging + PNG I/O + kornia grid_sample + publish.

All the imports that dominate cold-start (torch ~1.5s, kornia ~0.25s, cv2 ~0.1s,
plus CUDA context init ~0.9s) live in this module so the ``undistort.py`` client
entry can skip them entirely when the daemon is available.

Public entry: ``process_shard(request: dict, gpu_lock=None) -> (exit_code, log_lines)``.
The dict has the same fields as the CLI in ``undistort.py`` — the client
serialises argparse Namespace into it and either calls this in-process
(fallback path) or ships it to the daemon over a Unix socket.

Passing ``gpu_lock`` (a ``threading.Lock`` from the daemon) makes the GPU
section serial across concurrent shards; leaving it ``None`` (in-process
fallback) skips the lock since only one shard runs per subprocess anyway.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
import torch
import torch.nn.functional as F
from kornia.geometry.calibration import distort_points

_PACK_DIR = Path(__file__).resolve().parent
if str(_PACK_DIR) not in sys.path:
    sys.path.insert(0, str(_PACK_DIR))

from sfm_key import cam_key, numeric_suffix  # noqa: E402

# ---------------------------------------------------------------------------
# Staging (parity with @0.4.1)
# ---------------------------------------------------------------------------


def stage_images(source_dir: Path, scratch_input: Path) -> list[Path]:
    if scratch_input.exists():
        shutil.rmtree(scratch_input)
    scratch_input.mkdir(parents=True)
    if any(p.is_file() and not p.name.startswith(".") for p in source_dir.iterdir()):
        walk_root = source_dir
    else:
        walk_root = source_dir / "frames"
        if not walk_root.is_dir():
            raise SystemExit(f"no image files at {source_dir} or its frames/ subdir")

    staged: list[Path] = []
    for src in sorted(walk_root.iterdir()):
        if not (src.is_file() or src.is_symlink()):
            continue
        real = src.resolve()
        if not real.exists():
            raise SystemExit(f"broken image link: {src} -> {real}")
        dst = scratch_input / src.name
        try:
            os.link(real, dst)
        except OSError:
            shutil.copy2(real, dst)
        staged.append(dst)
    if not staged:
        raise SystemExit(f"no images staged from {source_dir}")
    return staged


def _parse_pose_rows(path: Path) -> tuple[list[str], list[tuple[list[str], str]]]:
    raw = path.read_text().splitlines()
    header: list[str] = []
    poses: list[tuple[list[str], str]] = []
    i = 0
    while i < len(raw):
        ln = raw[i]
        s = ln.strip()
        if not s or s.startswith("#"):
            header.append(ln)
            i += 1
            continue
        parts = s.split()
        if len(parts) < 10:
            i += 1
            continue
        obs = raw[i + 1] if i + 1 < len(raw) else ""
        poses.append((parts, obs))
        i += 2
    return header, poses


def _build_cam_file_map(files: list[Path]) -> tuple[dict[str, Path], dict[int, Path] | None]:
    by_key: dict[str, Path] = {}
    for f in files:
        by_key[cam_key(f.name)] = f
    by_num: dict[int, Path] | None = {}
    for key, f in by_key.items():
        n = numeric_suffix(key)
        if n is None or n in by_num:
            by_num = None
            break
        by_num[n] = f
    return by_key, by_num


def _resolve_file_for_sfm_name(
    name: str,
    by_key: dict[str, Path],
    by_num: dict[int, Path] | None,
) -> Path | None:
    sk = cam_key(name)
    if sk in by_key:
        return by_key[sk]
    if by_num is None:
        return None
    n = numeric_suffix(sk)
    if n is None:
        return None
    return by_num.get(n)


def stage_sparse_prior(
    sfm_cams_dir: Path,
    scratch_prior: Path,
    staged_files: list[Path],
) -> tuple[list[tuple[list[str], str]], list[tuple[str, str]], list[str]]:
    if scratch_prior.exists():
        shutil.rmtree(scratch_prior)
    scratch_prior.mkdir(parents=True)

    src_cams = sfm_cams_dir / "cameras.txt"
    src_imgs = sfm_cams_dir / "images.txt"
    if not src_cams.is_file() or not src_imgs.is_file():
        raise SystemExit(f"SfM cams dir missing cameras.txt / images.txt: {sfm_cams_dir}")
    shutil.copyfile(src_cams, scratch_prior / "cameras.txt")
    shutil.copyfile(src_imgs, scratch_prior / "images.txt")

    _, poses = _parse_pose_rows(src_imgs)
    by_key, by_num = _build_cam_file_map(staged_files)

    rewrites: list[tuple[str, str]] = []
    used: set[str] = set()
    rewritten: list[tuple[list[str], str]] = []
    absent_sfm_names: list[str] = []
    for row, obs in poses:
        orig = row[9]
        f = _resolve_file_for_sfm_name(orig, by_key, by_num)
        if f is None:
            absent_sfm_names.append(cam_key(orig))
            continue
        new_name = f.name
        if new_name in used:
            raise SystemExit(
                f"NAME rewrite collision: {orig!r} and an earlier row both point to "
                f"{new_name!r} in the staged set — cannot proceed"
            )
        used.add(new_name)
        row[9] = new_name
        rewritten.append((row, obs))
        rewrites.append((orig, new_name))
    return rewritten, rewrites, sorted(absent_sfm_names)


# ---------------------------------------------------------------------------
# Camera math
# ---------------------------------------------------------------------------


def parse_cameras(cameras_txt: Path) -> dict[int, dict]:
    """Parse models supported by the GPU radial undistorter.

    SIMPLE_RADIAL is exactly the OPENCV radial subset (f, cx, cy, k1), so
    represent it with fx=fy=f and zero unused OPENCV coefficients.
    """
    cams: dict[int, dict] = {}
    for ln in cameras_txt.read_text().splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split()
        model = parts[1]
        cid = int(parts[0])
        W, H = int(parts[2]), int(parts[3])
        p = [float(x) for x in parts[4:]]
        if model == "OPENCV" and len(p) == 8:
            fx, fy, cx, cy, k1, k2, p1, p2 = p
        elif model == "SIMPLE_RADIAL" and len(p) == 4:
            fx = fy = p[0]
            cx, cy, k1 = p[1:]
            k2 = p1 = p2 = 0.0
        else:
            raise SystemExit(
                f"unsupported camera {cid} model/params in {cameras_txt}: {model} {len(p)}"
            )
        cams[cid] = dict(
            cam_id=cid,
            W=W,
            H=H,
            fx=fx,
            fy=fy,
            cx=cx,
            cy=cy,
            k1=k1,
            k2=k2,
            p1=p1,
            p2=p2,
            source_model=model,
        )
    if not cams:
        raise SystemExit(f"no camera row in {cameras_txt}")
    return cams


def _iter_undistort(u: torch.Tensor, v: torch.Tensor, cam: dict, iters: int = 20):
    fx, fy, cx, cy = cam["fx"], cam["fy"], cam["cx"], cam["cy"]
    k1, k2, p1, p2 = cam["k1"], cam["k2"], cam["p1"], cam["p2"]
    xd = (u - cx) / fx
    yd = (v - cy) / fy
    x, y = xd.clone(), yd.clone()
    for _ in range(iters):
        r2 = x * x + y * y
        rad = 1 + k1 * r2 + k2 * r2 * r2
        dx = 2 * p1 * x * y + p2 * (r2 + 2 * x * x)
        dy = p1 * (r2 + 2 * y * y) + 2 * p2 * x * y
        x = (xd - dx) / rad
        y = (yd - dy) / rad
    return x, y


def compute_new_pinhole(cam: dict, blank_pixels: int = 0) -> dict:
    W, H = cam["W"], cam["H"]
    fx, fy = cam["fx"], cam["fy"]

    us = torch.arange(W, dtype=torch.float64) + 0.5
    vs = torch.arange(H, dtype=torch.float64) + 0.5
    x_left, _ = _iter_undistort(torch.full_like(vs, 0.5), vs, cam)
    x_right, _ = _iter_undistort(torch.full_like(vs, W - 0.5), vs, cam)
    _, y_top = _iter_undistort(us, torch.full_like(us, 0.5), cam)
    _, y_bot = _iter_undistort(us, torch.full_like(us, H - 0.5), cam)

    if blank_pixels == 0:
        left = float(x_left.max())
        right = float(x_right.min())
        top = float(y_top.max())
        bot = float(y_bot.min())
        half_x = min(abs(left), abs(right))
        half_y = min(abs(top), abs(bot))
    else:
        left = float(x_left.min())
        right = float(x_right.max())
        top = float(y_top.min())
        bot = float(y_bot.max())
        half_x = max(abs(left), abs(right))
        half_y = max(abs(top), abs(bot))

    new_W = round(2 * half_x * fx)
    new_H = round(2 * half_y * fy)
    return dict(W=new_W, H=new_H, fx=fx, fy=fy, cx=new_W / 2.0, cy=new_H / 2.0)


# ---------------------------------------------------------------------------
# GPU resample
# ---------------------------------------------------------------------------


def _build_grid(cam: dict, new_cam: dict, device: str) -> torch.Tensor:
    K = torch.tensor(
        [[cam["fx"], 0.0, cam["cx"]], [0.0, cam["fy"], cam["cy"]], [0.0, 0.0, 1.0]],
        device=device,
        dtype=torch.float32,
    )
    new_K = torch.tensor(
        [[new_cam["fx"], 0.0, new_cam["cx"]], [0.0, new_cam["fy"], new_cam["cy"]], [0.0, 0.0, 1.0]],
        device=device,
        dtype=torch.float32,
    )
    dist = torch.tensor(
        [cam["k1"], cam["k2"], cam["p1"], cam["p2"]],
        device=device,
        dtype=torch.float32,
    )
    us = torch.arange(new_cam["W"], device=device, dtype=torch.float32)
    vs = torch.arange(new_cam["H"], device=device, dtype=torch.float32)
    vv, uu = torch.meshgrid(vs, us, indexing="ij")
    pts = torch.stack([uu, vv], dim=-1).reshape(1, -1, 2)
    src_uv = distort_points(
        pts, K.unsqueeze(0), dist.unsqueeze(0), new_K=new_K.unsqueeze(0)
    ).reshape(1, new_cam["H"], new_cam["W"], 2)
    grid = torch.empty_like(src_uv)
    grid[..., 0] = src_uv[..., 0] / (cam["W"] - 1) * 2.0 - 1.0
    grid[..., 1] = src_uv[..., 1] / (cam["H"] - 1) * 2.0 - 1.0
    return grid


# Reusable pinned host buffers. Each request grows them if the shape got
# bigger; steady-state RSS is one input-chunk + one output shard, not one
# per shard. Without this, the daemon leaks ~90 MB per request (~9 GB after
# 100 shards → OOM-kills downstream STG under a 56 GB cgroup).
_PINNED_IN_BUF: torch.Tensor | None = None
_PINNED_OUT_BUF: torch.Tensor | None = None


def _pinned_in(chunk_shape: tuple[int, int, int, int]) -> torch.Tensor:
    global _PINNED_IN_BUF
    if _PINNED_IN_BUF is None or tuple(_PINNED_IN_BUF.shape) != chunk_shape:
        _PINNED_IN_BUF = torch.empty(chunk_shape, dtype=torch.uint8, pin_memory=True)
    return _PINNED_IN_BUF


def _pinned_out(out_shape: tuple[int, int, int, int]) -> torch.Tensor:
    global _PINNED_OUT_BUF
    if _PINNED_OUT_BUF is None or tuple(_PINNED_OUT_BUF.shape) != out_shape:
        _PINNED_OUT_BUF = torch.empty(out_shape, dtype=torch.uint8, pin_memory=True)
    return _PINNED_OUT_BUF


def undistort_batch(
    imgs_u8: np.ndarray,
    grid: torch.Tensor,
    chunk_size: int = 8,
    compute_dtype: torch.dtype = torch.float16,
) -> np.ndarray:
    B = imgs_u8.shape[0]
    new_H, new_W = int(grid.shape[1]), int(grid.shape[2])
    out_pinned = _pinned_out((B, new_H, new_W, 3))
    grid_c = grid.to(compute_dtype) if grid.dtype != compute_dtype else grid
    for s in range(0, B, chunk_size):
        e = min(s + chunk_size, B)
        chunk = imgs_u8[s:e]
        # Copy into a reusable pinned staging buffer instead of
        # ``.pin_memory()`` on a per-call tensor (which leaked in the daemon).
        in_pin = _pinned_in(chunk.shape)
        in_pin.copy_(torch.from_numpy(np.ascontiguousarray(chunk)))
        t_gpu = in_pin.to("cuda", non_blocking=True)
        imgs = t_gpu.permute(0, 3, 1, 2).to(compute_dtype).mul_(1.0 / 255.0)
        out = F.grid_sample(
            imgs,
            grid_c.expand(e - s, -1, -1, -1),
            mode="bilinear",
            padding_mode="zeros",
            align_corners=True,
        )
        out = (
            out.clamp_(0.0, 1.0)
            .mul_(255.0)
            .round_()
            .to(torch.uint8)
            .permute(0, 2, 3, 1)
            .contiguous()
        )
        out_pinned[s:e].copy_(out, non_blocking=True)
        del t_gpu, imgs, out
        torch.cuda.empty_cache()
    torch.cuda.synchronize()
    # Return a numpy VIEW of the pinned buffer — but numpy view goes stale
    # the next time the buffer is reused. Copy out so callers own it.
    return out_pinned.numpy().copy()


# ---------------------------------------------------------------------------
# I/O + output writers
# ---------------------------------------------------------------------------


def _read_rgb(path: Path) -> np.ndarray:
    bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if bgr is None:
        raise SystemExit(f"cv2.imread failed: {path}")
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def _write_rgb(dst: Path, rgb: np.ndarray, png_compress: int) -> None:
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    suffix = dst.suffix.lower()
    if suffix in (".jpg", ".jpeg"):
        ok = cv2.imwrite(str(dst), bgr, [cv2.IMWRITE_JPEG_QUALITY, 95])
    else:
        ok = cv2.imwrite(str(dst), bgr, [cv2.IMWRITE_PNG_COMPRESSION, png_compress])
    if not ok:
        raise SystemExit(f"cv2.imwrite failed: {dst}")


def write_cameras_pinhole(dst: Path, cameras: dict[int, dict]) -> None:
    lines = [
        "# Camera list with one line of data per camera:\n"
        "#   CAMERA_ID, MODEL, WIDTH, HEIGHT, PARAMS[]\n"
        f"# Number of cameras: {len(cameras)}\n"
    ]
    for cid, cam in sorted(cameras.items()):
        lines.append(
            f"{cid} PINHOLE {cam['W']} {cam['H']} {cam['fx']} {cam['fy']} {cam['cx']} {cam['cy']}\n"
        )
    dst.write_text("".join(lines))


def write_images_txt(
    dst: Path,
    poses: list[tuple[list[str], str]],
    mean_obs: float,
) -> None:
    lines: list[str] = [
        "# Image list with two lines of data per image:",
        "#   IMAGE_ID, QW, QX, QY, QZ, TX, TY, TZ, CAMERA_ID, NAME",
        "#   POINTS2D[] as (X, Y, POINT3D_ID)",
        f"# Number of images: {len(poses)}, mean observations per image: {mean_obs}",
    ]
    for row, obs in poses:
        lines.append(" ".join(row))
        lines.append(obs)
    dst.write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Top-level shard driver
# ---------------------------------------------------------------------------


def _ensure_cuda_warm() -> None:
    """Idempotent CUDA context init. Zero-cost after the first call."""
    if not torch.cuda.is_available():
        raise SystemExit("CUDA not available; this pack requires a GPU node")
    _ = torch.zeros(1, device="cuda") + 1
    torch.cuda.synchronize()


def process_shard(
    request: dict,
    gpu_lock=None,
    log_fn=print,
) -> int:
    """Run one shard end-to-end.

    ``request`` fields (all strings unless noted): cams, images, cams_out,
    images_out, scratch, staging (optional / None), blank_pixels (int),
    io_workers (int), png_compress (int), chunk_size (int), dtype ('fp16'|'fp32').

    ``gpu_lock``: if provided, the GPU section (build_grid + undistort_batch)
    runs under this lock. Read/write PNG stages run outside the lock so
    concurrent daemon requests can overlap their I/O.

    ``log_fn``: sink for ``[image-undistort/0.5] ...`` lines. Defaults to
    stdout; the daemon passes a per-request buffer so lines flow back to
    the client for parity with in-process invocation.
    """
    cams = Path(request["cams"])
    images = Path(request["images"])
    cams_out = Path(request["cams_out"])
    images_out = Path(request["images_out"])
    scratch = Path(request["scratch"])
    staging = Path(request["staging"]) if request.get("staging") else None
    blank_pixels = int(request["blank_pixels"])
    io_workers = int(request["io_workers"])
    png_compress = int(request["png_compress"])
    dtype = str(request["dtype"])

    if not images.is_dir():
        log_fn(f"ERROR: images handle is not a directory: {images}", file=sys.stderr)
        return 2
    if blank_pixels not in (0, 1):
        log_fn(
            f"ERROR: blank_pixels={blank_pixels}; only 0 and 1 supported.",
            file=sys.stderr,
        )
        return 2

    cams_out.mkdir(parents=True, exist_ok=True)
    images_out.mkdir(parents=True, exist_ok=True)
    scratch.mkdir(parents=True, exist_ok=True)
    staging_root = staging if staging is not None else scratch
    staging_root.mkdir(parents=True, exist_ok=True)
    scratch_input = staging_root / "input"
    scratch_prior = staging_root / "prior"

    t_all0 = time.perf_counter()

    staged = stage_images(images, scratch_input)
    rewritten, rewrites, absent_sfm_keys = stage_sparse_prior(cams, scratch_prior, staged)
    if len(rewritten) < 2:
        raise SystemExit(
            f"need at least 2 staged images with SfM poses to undistort; got {len(rewritten)}"
        )
    staged = [f for f in staged if f.name in {row[9] for row, _obs in rewritten}]

    source_cams = parse_cameras(scratch_prior / "cameras.txt")
    file_cam_id = {row[9]: int(row[8]) for row, _obs in rewritten}
    if set(f.name for f in staged) != set(file_cam_id):
        raise SystemExit("staged image names do not match rewritten images.txt")
    used_ids = {file_cam_id[f.name] for f in staged}
    new_cams = {
        cid: compute_new_pinhole(source_cams[cid], blank_pixels=blank_pixels) for cid in used_ids
    }
    log_fn(
        f"[image-undistort/0.5] {len(used_ids)} source camera(s), {len(staged)} images", flush=True
    )
    log_fn(
        "[image-undistort/0.5] using "
        + ", ".join(sorted(f.name for f in staged))
        + "; absent SfM cameras: "
        + (", ".join(absent_sfm_keys) if absent_sfm_keys else "(none)"),
        flush=True,
    )

    t_r0 = time.perf_counter()
    with ThreadPoolExecutor(io_workers) as ex:
        arrs = list(ex.map(_read_rgb, staged))
    for f, a in zip(staged, arrs, strict=True):
        cam = source_cams[file_cam_id[f.name]]
        if a.shape[0] != cam["H"] or a.shape[1] != cam["W"]:
            raise SystemExit(
                f"image {f.name} shape {a.shape[:2]} != source (H,W)=({cam['H']},{cam['W']})"
            )
    t_r1 = time.perf_counter()

    _ensure_cuda_warm()
    compute_dtype = torch.float16 if dtype == "fp16" else torch.float32

    if gpu_lock is not None:
        gpu_lock.acquire()
    try:
        t_g0 = time.perf_counter()
        out_parts = []
        for f, img in zip(staged, arrs, strict=True):
            cid = file_cam_id[f.name]
            grid = _build_grid(source_cams[cid], new_cams[cid], "cuda")
            out_parts.append(
                undistort_batch(img[None, ...], grid, chunk_size=1, compute_dtype=compute_dtype)[0]
            )
        # Different source intrinsics legitimately produce different valid
        # PINHOLE crops, so this is a list rather than an N-by-H-by-W tensor.
        out = out_parts
        torch.cuda.synchronize()
        t_g1 = time.perf_counter()
    finally:
        if gpu_lock is not None:
            gpu_lock.release()

    out_root = images_out
    for stale in out_root.glob("*"):
        if stale.is_file() and not stale.name.startswith("."):
            stale.unlink()
    t_w0 = time.perf_counter()
    with ThreadPoolExecutor(io_workers) as ex:
        list(
            ex.map(
                lambda pair: _write_rgb(out_root / pair[0], pair[1], png_compress),
                zip([f.name for f in staged], out, strict=True),
            )
        )
    t_w1 = time.perf_counter()

    write_cameras_pinhole(cams_out / "cameras.txt", new_cams)
    mean_obs = 0.0
    header_src = (scratch_prior / "images.txt").read_text().splitlines()
    for ln in header_src:
        if "mean observations per image:" in ln:
            with contextlib.suppress(ValueError):
                mean_obs = float(ln.rsplit(":", 1)[1].strip())
            break
    write_images_txt(cams_out / "images.txt", rewritten, mean_obs)
    (cams_out / "points3D.txt").write_text("")
    (cams_out / "undistortion_meta.json").write_text(
        json.dumps(
            {
                "used_image_files": sorted(f.name for f in staged),
                "absent_sfm_camera_keys": absent_sfm_keys,
                "used_camera_count": len(staged),
            },
            indent=2,
        )
        + "\n"
    )

    t_all1 = time.perf_counter()

    fallback_hits = sum(1 for orig, new in rewrites if cam_key(orig) != Path(new).stem)
    log_fn(
        f"[image-undistort/0.5] done -> PINHOLE cams + {len(staged)} undistorted images  "
        f"read={1e3 * (t_r1 - t_r0):.0f} gpu={1e3 * (t_g1 - t_g0):.0f} "
        f"write={1e3 * (t_w1 - t_w0):.0f} total={1e3 * (t_all1 - t_all0):.0f} ms  "
        f"(NAME rewrites: {len(rewrites)}; via numeric bridge: {fallback_hits})",
        flush=True,
    )
    return 0
