#!/usr/bin/env python3
"""Blend two image groups by array index, with no filename matching or resizing."""
from __future__ import annotations

import argparse
import math
import os
from pathlib import Path

from PIL import Image


def image_group(root: Path) -> list[Path]:
    """HoloLab order: visible basenames sorted by filesystem bytes, not link targets."""
    entries = sorted((p for p in root.iterdir() if not p.name.startswith('.')),
                     key=lambda p: os.fsencode(p.name))
    if not entries:
        raise ValueError(f'Empty image group: {root}')
    for p in entries:
        if not p.is_file():
            raise ValueError(f'Expected image file, got directory/broken link/special file: {p}')
    return entries


def overlay_group(image: Path, mask: Path, output: Path, alpha: float = 0.5) -> int:
    if not math.isfinite(alpha) or not 0 <= alpha <= 1:
        raise ValueError('alpha must be finite and in [0, 1]')
    images, masks = image_group(image), image_group(mask)
    if len(images) != len(masks):
        raise ValueError(f'Image/mask count mismatch: {len(images)} != {len(masks)}')
    # Check every pair before writing; never leave a success marker for invalid input.
    for i, (src, msk) in enumerate(zip(images, masks)):
        with Image.open(src) as im, Image.open(msk) as ma:
            if im.size != ma.size:
                raise ValueError(f'Size mismatch at index {i}: image {im.size} != mask {ma.size}')
            im.verify()
            ma.verify()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError(f'Output must be empty: {output}')
    for i, (src, msk) in enumerate(zip(images, masks)):
        with Image.open(src) as im, Image.open(msk) as ma:
            blended = Image.blend(im.convert('RGB'), ma.convert('RGB'), alpha)
            blended.save(output / f'p_{i:016d}.png', compress_level=1)
        print(f'[ITER {i + 1}/{len(images)}]', flush=True)
    (output / '.hololab-done').touch()
    return len(images)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['image', 'mask', 'output']:
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--alpha', type=float, default=0.5)
    args = parser.parse_args()
    overlay_group(args.image, args.mask, args.output, args.alpha)


if __name__ == '__main__':
    main()
