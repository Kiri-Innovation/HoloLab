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

import cv2
import numpy as np
from tqdm import tqdm

# BT.2020 → BT.709 primaries conversion (Rec.709/sRGB uses BT.709 primaries).
# iPhone HEVC is HLG / BT.2020; downstream PIL/cv2/sRGB display chains
# interpret pixels as sRGB, so skipping OETF inversion + gamut mapping
# yields washed-out, greenish-yellow output. Standard matrix per Rec.709.
_BT2020_TO_BT709 = np.array(
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
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=color_transfer",
            "-of", "default=noprint_wrappers=1:nokey=1",
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

    linear_709 = scene @ _BT2020_TO_BT709.T
    linear_709 = np.clip(linear_709, 0.0, 1.0)

    # sRGB OETF (IEC 61966-2-1): linear → sRGB encoded.
    threshold = 0.0031308
    srgb = np.where(
        linear_709 <= threshold,
        12.92 * linear_709,
        1.055 * np.power(np.maximum(linear_709, 0.0), 1.0 / 2.4) - 0.055,
    )
    return np.clip(srgb * 255.0, 0.0, 255.0).astype(np.uint8)


def extract_frames(
    video_path: Path,
    output_dir: Path,
    frame_skip: int = 1,
    max_frames: int | None = None,
) -> int:
    """Decode ``video_path`` into ``output_dir/frames/frame_XXXXXX.png``.

    Idempotent: if ``frames/`` already contains ``frame_*.png``, returns the
    existing count without re-decoding — delete the directory to force a
    re-extract.
    """
    frames_dir = output_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    existing = list(frames_dir.glob("frame_*.png"))
    if existing:
        print(f"   ✓ frames/ 已有 {len(existing)} 帧，跳过抽帧")
        return len(existing)

    # ffprobe for metadata (avoids cv2 HEVC 10-bit decode bugs).
    ffprobe_cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=nb_frames,r_frame_rate",
        "-of", "default=noprint_wrappers=1", str(video_path),
    ]
    total_frames: int | None = None
    fps = 30.0
    try:
        ffprobe_out = subprocess.run(
            ffprobe_cmd, capture_output=True, text=True, timeout=15
        ).stdout
        for line in ffprobe_out.splitlines():
            if line.startswith("r_frame_rate="):
                num, den = line.split("=")[1].split("/")
                fps = float(num) / float(den) if float(den) != 0 else 30.0
            elif line.startswith("nb_frames="):
                with contextlib.suppress(ValueError):
                    total_frames = int(line.split("=")[1])
    except Exception:
        pass

    print(f"   视频: {video_path.name} · fps={fps:.2f}"
          + (f" · 总帧={total_frames}" if total_frames else ""))
    if frame_skip > 1:
        print(f"   跳帧: 每 {frame_skip} 帧取 1 帧")
    if max_frames:
        print(f"   上限: {max_frames} 帧")

    # HDR detection: iPhone HEVC is often HLG + BT.2020. Raw ffmpeg→PNG
    # skips OETF inversion and gamut mapping, so PNGs look washed out.
    # HDR path uses 16-bit RGB, then Python-side HLG/PQ decode + BT.2020→709 + sRGB OETF.
    hdr_transfer = _detect_hdr_transfer(video_path)
    if hdr_transfer:
        print(f"   ⚠ HDR (transfer={hdr_transfer}) → 做 HDR→SDR tonemap")

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video_path),
    ]
    if frame_skip > 1:
        cmd += ["-vf", "select='not(mod(n\\," + str(frame_skip) + "))'", "-vsync", "vfr"]
    if max_frames:
        cmd += ["-frames:v", str(max_frames)]
    if hdr_transfer:
        # 16-bit big-endian RGB; ffmpeg does YUV→RGB with the BT.2020 matrix
        # but leaves transfer/primaries alone, so we still hold HLG/PQ-encoded
        # BT.2020 pixels for the Python-side conversion.
        cmd += ["-pix_fmt", "rgb48be"]
    cmd += [str(frames_dir / "tmp_frame_%06d.png")]

    subprocess.run(cmd, check=True)

    tmp_files = sorted(frames_dir.glob("tmp_frame_*.png"))
    saved = 0
    desc = "抽帧+HDR tonemap" if hdr_transfer else "抽帧"
    with tqdm(total=len(tmp_files), desc=desc) as pbar:
        for src in tmp_files:
            dst = frames_dir / f"frame_{saved:06d}.png"
            if hdr_transfer:
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
        description="从视频抽帧到 <output>/frames/ (frame_XXXXXX.png)。"
    )
    parser.add_argument("video", type=str, help="输入视频路径。")
    parser.add_argument(
        "-o", "--output", type=str, required=True,
        help="输出目录; 帧写入 <output>/frames/。",
    )
    parser.add_argument(
        "--skip", type=int, default=1,
        help="每 N 帧提取一帧 (默认 1 = 全部)。",
    )
    parser.add_argument(
        "--max-frames", type=int, default=None,
        help="最多提取帧数 (与 --skip 叠加; 默认无上限)。",
    )
    args = parser.parse_args()

    video_path = Path(args.video)
    if not video_path.is_file():
        print(f"✗ 输入视频不存在: {video_path}", file=sys.stderr)
        return 2

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[ITER 1/1] 抽帧: {video_path}")
    n = extract_frames(
        video_path,
        output_dir,
        frame_skip=args.skip,
        max_frames=args.max_frames,
    )
    print(f"✓ 抽出 {n} 帧 → {output_dir / 'frames'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
