#!/usr/bin/env python3
# ruff: noqa: RUF001
"""Standalone video → frame_XXXXXX.png extractor for the frame-extraction pack.

Zero deps on any algorithm repo. Only stdlib + cv2 + numpy + tqdm + ffmpeg/
ffprobe binaries. HDR (HLG / PQ) videos are tonemapped to sRGB automatically
so downstream image-space algorithms see a well-defined color space.

Lifted from sharp-4dgs/per-frame/video_to_colmap.py::extract_frames — see
the migration note in hololab/docs/writing-a-pack.md for why this now lives
alongside the pack manifest instead of in the algorithm repo.
"""

from __future__ import annotations

import argparse
import contextlib
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

# cv2 / numpy / tqdm are imported lazily inside the HDR tonemap and the
# extraction driver — they aren't needed for argument-parsing, the pure-Python
# ``_build_select_expr`` helper, or the SDR-passthrough happy path. Keeping
# module-level imports narrow means unit tests can load this module in
# environments without OpenCV installed.


def _bt2020_to_bt709_matrix() -> np.ndarray:
    """BT.2020 → BT.709 primaries conversion matrix (Rec.709/sRGB uses BT.709).

    iPhone HEVC is HLG / BT.2020; downstream PIL/cv2/sRGB display chains
    interpret pixels as sRGB, so skipping OETF inversion + gamut mapping
    yields washed-out, greenish-yellow output. Standard matrix per Rec.709.
    """
    import numpy as np

    return np.array(
        [
            [1.6604910, -0.5876411, -0.0728499],
            [-0.1245504, 1.1328999, -0.0083494],
            [-0.0181508, -0.1005789, 1.1187297],
        ],
        dtype=np.float32,
    )


def _detect_hdr_transfer(video_path: Path) -> str | None:
    """Detect HDR transfer characteristic; returns 'hlg' / 'pq' / None (SDR)."""
    try:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=color_transfer",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15).stdout.strip().lower()
    except Exception:
        return None
    if out in ("arib-std-b67", "hlg"):
        return "hlg"
    if out in ("smpte2084", "pq"):
        return "pq"
    return None


def _hdr_bt2020_to_srgb_uint8(rgb16: np.ndarray, transfer: str) -> np.ndarray:
    """16-bit HLG/PQ + BT.2020 RGB → 8-bit sRGB (display-ready).

    Input: (H,W,3) uint16, BT.2020 primaries, HLG (arib-std-b67) or PQ
    (smpte2084) OETF/EOTF. Output: (H,W,3) uint8 sRGB.
    """
    import numpy as np

    e = rgb16.astype(np.float32) / 65535.0

    if transfer == "hlg":
        # HLG inverse OETF (ARIB STD-B67 / Rec.2100): E' → scene-linear E ∈ [0,1].
        # SDR target (100 nit) makes system gamma = 1.0, so scene-linear passes through.
        a = 0.17883277
        b = 1.0 - 4.0 * a
        c = 0.5 - a * float(np.log(4.0 * a))
        scene = np.where(e <= 0.5, (e * e) / 3.0, (np.exp((e - c) / a) + b) / 12.0)
    elif transfer == "pq":
        # PQ inverse EOTF (SMPTE 2084): E' → display-linear, 1.0 = 10000 nits.
        m1 = 2610.0 / 16384.0
        m2 = 2523.0 / 4096.0 * 128.0
        c1 = 3424.0 / 4096.0
        c2 = 2413.0 / 4096.0 * 32.0
        c3 = 2392.0 / 4096.0 * 32.0
        p = np.power(np.maximum(e, 0.0), 1.0 / m2)
        scene = np.maximum(p - c1, 0.0) / np.maximum(c2 - c3 * p, 1e-9)
        scene = np.power(scene, 1.0 / m1)
        # Reinhard soft-compression from HDR peak (~1000 nit) down to SDR (~100 nit).
        scene = scene * 100.0
        scene = scene / (1.0 + scene)
    else:
        scene = e

    linear_709 = scene @ _bt2020_to_bt709_matrix().T
    linear_709 = np.clip(linear_709, 0.0, 1.0)

    # sRGB OETF (IEC 61966-2-1): linear → sRGB encoded.
    threshold = 0.0031308
    srgb = np.where(
        linear_709 <= threshold,
        12.92 * linear_709,
        1.055 * np.power(np.maximum(linear_709, 0.0), 1.0 / 2.4) - 0.055,
    )
    return np.clip(srgb * 255.0, 0.0, 255.0).astype(np.uint8)


def _build_select_expr(start_frame: int, end_frame: int, frame_skip: int) -> str | None:
    """Build the ffmpeg ``select`` filter expression for window + skip stride.

    Semantics mirror ``SpacetimeGaussians/script/pre_n3d.py::extractframes``:
    the window ``[start_frame, end_frame)`` is applied in source-frame-index
    space (0-based), and the stride picks every ``frame_skip``-th frame
    counted from ``start_frame`` (so ``start=3, skip=2`` yields n=3,5,7…).

    ``end_frame=0`` means "no upper bound"; ``start_frame=0`` means "from
    the beginning". Returns ``None`` when nothing needs filtering (skip=1,
    full window) so the caller can skip the ``-vf select`` altogether and
    stay on the fast passthrough path.
    """
    parts: list[str] = []
    if start_frame > 0:
        parts.append(f"gte(n,{start_frame})")
    if end_frame > 0:
        parts.append(f"lt(n,{end_frame})")
    if frame_skip > 1:
        # Stride counted from start_frame so window-first-frame is always kept.
        parts.append(f"not(mod(n-{start_frame},{frame_skip}))")
    return "*".join(parts) if parts else None


def extract_frames(
    video_path: Path,
    output_dir: Path,
    frame_skip: int = 1,
    max_frames: int | None = None,
    max_width: int | None = None,
    start_frame: int = 0,
    end_frame: int | None = None,
) -> int:
    """Decode ``video_path`` into ``output_dir/frame_XXXXXX.png``.

    Per the flatten migration the shard IS the ``arrayed<image>`` — image
    files land directly under ``output_dir`` with no intermediate
    ``frames/`` wrapper.

    Extra params (all match STG pre_no_prior semantics):
      * ``max_width``    downscale to this pixel width (aspect-preserved);
                         ``None`` / ``0`` = keep native resolution.
                         Only ever downscales — a video narrower than
                         ``max_width`` passes through untouched.
      * ``start_frame``  source-index (inclusive) window start.
      * ``end_frame``    source-index (exclusive) window end;
                         ``None`` / ``0`` = no end, extract to video tail.

    Idempotent: if ``frame_*.png`` already sit under ``output_dir``,
    return the existing count without re-decoding — delete them to force
    a re-extract.
    """
    frames_dir = output_dir
    frames_dir.mkdir(parents=True, exist_ok=True)

    # Normalize sentinel zeros to None so downstream logic is uniform.
    max_width_val = max_width if (max_width or 0) > 0 else None
    end_frame_val = end_frame if (end_frame or 0) > 0 else None

    if end_frame_val is not None and start_frame >= end_frame_val:
        raise ValueError(f"start_frame ({start_frame}) must be < end_frame ({end_frame_val})")

    existing = list(frames_dir.glob("frame_*.png"))
    if existing:
        print(f"   ✓ 已有 {len(existing)} 帧，跳过抽帧")
        return len(existing)

    # ffprobe for metadata (avoids cv2 HEVC 10-bit decode bugs).
    ffprobe_cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=nb_frames,r_frame_rate",
        "-of",
        "default=noprint_wrappers=1",
        str(video_path),
    ]
    total_frames: int | None = None
    fps = 30.0
    try:
        ffprobe_out = subprocess.run(ffprobe_cmd, capture_output=True, text=True, timeout=15).stdout
        for line in ffprobe_out.splitlines():
            if line.startswith("r_frame_rate="):
                num, den = line.split("=")[1].split("/")
                fps = float(num) / float(den) if float(den) != 0 else 30.0
            elif line.startswith("nb_frames="):
                with contextlib.suppress(ValueError):
                    total_frames = int(line.split("=")[1])
    except Exception:
        pass

    print(
        f"   视频: {video_path.name} · fps={fps:.2f}"
        + (f" · 总帧={total_frames}" if total_frames else "")
    )
    if start_frame > 0 or end_frame_val is not None:
        end_str = str(end_frame_val) if end_frame_val is not None else "end"
        print(f"   帧窗口: [{start_frame}, {end_str})")
    if frame_skip > 1:
        print(f"   跳帧: 每 {frame_skip} 帧取 1 帧")
    if max_frames:
        print(f"   上限: {max_frames} 帧")
    if max_width_val is not None:
        print(f"   下采样上限宽: {max_width_val}px（保持宽高比，仅缩小）")

    # HDR detection: iPhone HEVC is often HLG + BT.2020. Raw ffmpeg→PNG
    # skips OETF inversion and gamut mapping, so PNGs look washed out.
    # HDR path uses 16-bit RGB, then Python-side HLG/PQ decode + BT.2020→709 + sRGB OETF.
    hdr_transfer = _detect_hdr_transfer(video_path)
    if hdr_transfer:
        print(f"   ⚠ HDR (transfer={hdr_transfer}) → 做 HDR→SDR tonemap")

    # Compose the -vf filter chain: select (window+stride) → scale (max_width).
    # Order matters: filter frames first, then downscale the survivors.
    filters: list[str] = []
    select_expr = _build_select_expr(start_frame, end_frame_val or 0, frame_skip)
    if select_expr is not None:
        # Escape commas in the expression for ffmpeg's filter parser.
        filters.append(f"select='{select_expr.replace(',', chr(92) + ',')}'")
    if max_width_val is not None:
        # min(iw,W) mirrors the original ``if frame.shape[1] > max_width`` guard —
        # never upscale. ``-2`` on height auto-computes preserving aspect ratio.
        filters.append(f"scale='min(iw\\,{max_width_val})':-2:flags=lanczos")

    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
    ]
    if filters:
        cmd += ["-vf", ",".join(filters)]
    if select_expr is not None:
        # ``select`` produces non-monotonic PTS; vfr keeps them 1:1 with frames.
        cmd += ["-vsync", "vfr"]
    if max_frames:
        cmd += ["-frames:v", str(max_frames)]
    if hdr_transfer:
        # 16-bit big-endian RGB; ffmpeg does YUV→RGB with the BT.2020 matrix
        # but leaves transfer/primaries alone, so we still hold HLG/PQ-encoded
        # BT.2020 pixels for the Python-side conversion.
        cmd += ["-pix_fmt", "rgb48be"]
    cmd += [str(frames_dir / "tmp_frame_%06d.png")]

    subprocess.run(cmd, check=True)

    # Heavy deps imported lazily so unit tests (window/scale expr checks) can
    # exercise this module without OpenCV / numpy present in the venv.
    from tqdm import tqdm

    tmp_files = sorted(frames_dir.glob("tmp_frame_*.png"))
    saved = 0
    desc = "抽帧+HDR tonemap" if hdr_transfer else "抽帧"
    with tqdm(total=len(tmp_files), desc=desc) as pbar:
        for src in tmp_files:
            dst = frames_dir / f"frame_{saved:06d}.png"
            if hdr_transfer:
                import cv2
                import numpy as np

                img16_bgr = cv2.imread(str(src), cv2.IMREAD_UNCHANGED)
                if img16_bgr is None or img16_bgr.dtype != np.uint16:
                    raise RuntimeError(
                        f"HDR tonemap 期望 16-bit PNG, 实际读到 "
                        f"{None if img16_bgr is None else img16_bgr.dtype} @ {src}"
                    )
                img16_rgb = cv2.cvtColor(img16_bgr, cv2.COLOR_BGR2RGB)
                img8_rgb = _hdr_bt2020_to_srgb_uint8(img16_rgb, hdr_transfer)
                cv2.imwrite(str(dst), cv2.cvtColor(img8_rgb, cv2.COLOR_RGB2BGR))
                src.unlink()
            else:
                shutil.move(str(src), str(dst))
            saved += 1
            pbar.update(1)

    return saved


def main() -> int:
    parser = argparse.ArgumentParser(
        description="从视频抽帧到 <output>/frame_XXXXXX.png (flatten migration: no frames/)。"
    )
    parser.add_argument("video", type=str, help="输入视频路径。")
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        required=True,
        help="输出目录; 帧直接写入 <output>/frame_XXXXXX.png。",
    )
    parser.add_argument(
        "--skip",
        type=int,
        default=1,
        help="每 N 帧提取一帧 (默认 1 = 全部)。窗口内的步长。",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=None,
        help="最多提取帧数 (与 --skip 叠加; 默认无上限)。",
    )
    parser.add_argument(
        "--max-width",
        type=int,
        default=0,
        help=(
            "空间下采样上限宽 (px)。输入更宽时按比例缩到该宽度；"
            "0 或未设 = 保持原分辨率。对齐 STG pre_no_prior.py 语义。"
        ),
    )
    parser.add_argument(
        "--start-frame",
        type=int,
        default=0,
        help="源帧窗口起点 (0-based, 含)。对齐 STG --startframe。",
    )
    parser.add_argument(
        "--end-frame",
        type=int,
        default=0,
        help=("源帧窗口终点 (0-based, 不含)。0 = 无终点，抽到视频尾。对齐 STG --endframe。"),
    )
    args = parser.parse_args()

    video_path = Path(args.video)
    if not video_path.is_file():
        print(f"✗ 输入视频不存在: {video_path}", file=sys.stderr)
        return 2

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[ITER 1/1] 抽帧: {video_path}")
    try:
        n = extract_frames(
            video_path,
            output_dir,
            frame_skip=args.skip,
            max_frames=args.max_frames,
            max_width=args.max_width,
            start_frame=args.start_frame,
            end_frame=args.end_frame,
        )
    except ValueError as exc:
        print(f"✗ 参数错误: {exc}", file=sys.stderr)
        return 2
    print(f"✓ 抽出 {n} 帧 → {output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
