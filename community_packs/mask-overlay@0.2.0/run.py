#!/usr/bin/env python3
"""Bounded parallel mask visualization; each invocation consumes two flat image arrays."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import math
import os
from pathlib import Path
import time

import cv2
import numpy as np
from PIL import Image

MODES = ('mask0_black', 'mask0_red', 'mask1_red', 'blend50')


def image_group(root: Path) -> list[Path]:
    entries = sorted((p for p in root.iterdir() if not p.name.startswith('.')),
                     key=lambda p: os.fsencode(p.name))
    if not entries:
        raise ValueError(f'Empty image group: {root}')
    for p in entries:
        if not p.is_file():
            raise ValueError(f'Expected image file, got directory/broken link/special file: {p}')
    return entries


def render_bgr(image: np.ndarray, mask: np.ndarray, mode: str, alpha: float) -> np.ndarray:
    """uint8 BGR images; L masks stay single-channel. Output truncates fractional values."""
    if mode not in MODES:
        raise ValueError(f'Invalid mode: {mode}')
    if not math.isfinite(alpha) or not 0 <= alpha <= 1:
        raise ValueError('alpha must be finite and in [0, 1]')
    if image.dtype != np.uint8 or mask.dtype != np.uint8:
        raise ValueError('Expected 8-bit images and masks')
    if image.shape[:2] != mask.shape[:2]:
        raise ValueError(f'Size mismatch: image {image.shape[:2]} != mask {mask.shape[:2]}')
    if mask.ndim == 3 and mask.shape[2] == 4:
        mask = mask[:, :, :3]
    if mode == 'blend50':
        other = mask[:, :, None] if mask.ndim == 2 else mask
        if alpha == 0.5:
            # Exact legacy floor((image + mask_rgb) / 2), without a full RGB mask copy.
            out = np.add(image, other, dtype=np.uint16)
            np.right_shift(out, 1, out=out)
            return out.astype(np.uint8)
        return ((1 - alpha) * image.astype(np.float64) + alpha * other).astype(np.uint8)
    gray = mask if mask.ndim == 2 else cv2.cvtColor(mask, cv2.COLOR_BGR2GRAY)
    selected = np.asarray(gray >= 128 if mode == 'mask1_red' else gray < 128, dtype=np.uint8)
    color = np.array([0, 0, 0] if mode == 'mask0_black' else [0, 0, 255])
    # A 768-byte LUT avoids large float images and repeated color conversions.
    lut = ((1 - alpha) * np.arange(256)[:, None] + alpha * color).astype(np.uint8)
    tinted = cv2.LUT(image, lut.reshape(256, 1, 3))
    out = image.copy()
    cv2.copyTo(tinted, selected, out)
    return out


def process_pair(index, src, mask, output, mode, alpha, fmt, png_compress, jpeg_quality):
    started = time.perf_counter()
    image = cv2.imread(str(src), cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
    binary = cv2.imread(str(mask), cv2.IMREAD_UNCHANGED)
    if image is None or binary is None:
        raise ValueError(f'Image decode failed at index {index}: {src}, {mask}')
    decoded = time.perf_counter()
    result = render_bgr(image, binary, mode, alpha)
    blended = time.perf_counter()
    params = ([cv2.IMWRITE_PNG_COMPRESSION, png_compress] if fmt == 'png'
              else [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
    dest = output / f'p_{index:016d}.{fmt}'
    if not cv2.imwrite(str(dest), result, params):
        raise OSError(f'Image write failed: {dest}')
    return {'decode_s': decoded - started, 'blend_s': blended - decoded,
            'encode_write_s': time.perf_counter() - blended, 'bytes': dest.stat().st_size}


def overlay_group(image: Path, mask: Path, output: Path, alpha=0.5, mode='mask0_black',
                  io_workers=3, format='png', png_compress=1, jpeg_quality=95) -> int:
    started = time.perf_counter()
    if mode not in MODES or format not in ('png', 'jpg'):
        raise ValueError(f'Invalid mode/format: {mode}/{format}')
    if not math.isfinite(alpha) or not 0 <= alpha <= 1:
        raise ValueError('alpha must be finite and in [0, 1]')
    for name, value, low, high in [('io_workers', io_workers, 1, 16),
                                  ('png_compress', png_compress, 0, 9),
                                  ('jpeg_quality', jpeg_quality, 1, 100)]:
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f'{name} must be an integer in [{low}, {high}]')
    images, masks = image_group(image), image_group(mask)
    if len(images) != len(masks):
        raise ValueError(f'Image/mask count mismatch: {len(images)} != {len(masks)}')
    # Header-only preflight catches dimension errors before any writes. Full decode happens once.
    for i, (src, msk) in enumerate(zip(images, masks)):
        with Image.open(src) as im, Image.open(msk) as ma:
            if im.size != ma.size:
                raise ValueError(f'Size mismatch at index {i}: image {im.size} != mask {ma.size}')
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError(f'Output must be empty: {output}')
    cv2.setNumThreads(1)
    totals = dict(decode_s=0.0, blend_s=0.0, encode_write_s=0.0, bytes=0)
    with ThreadPoolExecutor(max_workers=io_workers) as executor:
        futures = [executor.submit(process_pair, i, src, msk, output, mode, alpha,
                                   format, png_compress, jpeg_quality)
                   for i, (src, msk) in enumerate(zip(images, masks))]
        try:
            for completed, future in enumerate(as_completed(futures), 1):
                for key, value in future.result().items():
                    totals[key] += value
                print(f'[ITER {completed}/{len(images)}]', flush=True)
        except BaseException:
            for future in futures:
                future.cancel()
            raise
    elapsed = time.perf_counter() - started
    report = {'count': len(images), 'mode': mode, 'alpha': alpha, 'format': format,
              'io_workers': io_workers, 'elapsed_seconds': elapsed,
              'images_per_second': len(images) / elapsed,
              'worker_seconds_sum': totals}
    (output / '.metrics.json').write_text(json.dumps(report, indent=2) + '\n')
    (output / '.hololab-done').touch()
    print(json.dumps(report), flush=True)
    return len(images)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ['image', 'mask', 'output']:
        p.add_argument('--' + name, required=True, type=Path)
    p.add_argument('--alpha', type=float, default=0.5)
    p.add_argument('--mode', choices=MODES, default='mask0_black')
    p.add_argument('--io-workers', type=int, default=3)
    p.add_argument('--format', choices=('png', 'jpg'), default='png')
    p.add_argument('--png-compress', type=int, default=1)
    p.add_argument('--jpeg-quality', type=int, default=95)
    a = p.parse_args()
    overlay_group(a.image, a.mask, a.output, a.alpha, a.mode, a.io_workers,
                  a.format, a.png_compress, a.jpeg_quality)


if __name__ == '__main__':
    main()
