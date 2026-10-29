#!/usr/bin/env python3
"""Crop/pad a still image in stored pixel coordinates, without resampling."""

import argparse
import math
import os
import re
import shutil
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image


def pixel_padding(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("padding must be finite")
    return int(math.copysign(math.floor(abs(value) + 0.5), value))


def crop_image(source, output, pads=(0, 0, 0, 0), pad_color="#000000"):
    source, output = Path(source), Path(output)
    if source.resolve() == output.resolve():
        raise ValueError("input and output must be different files")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", pad_color):
        raise ValueError("pad_color must be #RRGGBB (for example #000000)")
    rgb = tuple(int(pad_color[i : i + 2], 16) for i in (1, 3, 5))
    left, top, right, bottom = map(pixel_padding, pads)
    with Image.open(source) as im:
        fmt = im.format
        if fmt not in ("PNG", "JPEG", "WEBP"):
            raise ValueError("image must be PNG, JPEG (.jpg/.jpeg), or WebP")
        if getattr(im, "n_frames", 1) != 1:
            raise ValueError("animated images are unsupported; provide one still image")
        w, h = im.size
        # Crop against the original extent before allowing outward padding.
        x0, y0 = min(w, max(0, left)), min(h, max(0, top))
        x1, y1 = max(0, w - max(0, right)), max(0, h - max(0, bottom))
        if x1 <= x0 or y1 <= y0:
            raise ValueError(
                f"crop removes entire image: source={w}x{h}, rounded paddings L/T/R/B={left}/{top}/{right}/{bottom}; retain at least 1x1 original pixel"
            )
        ow, oh = w - left - right, h - top - bottom
        if ow > 100000 or oh > 100000 or ow * oh > 100000000:
            raise ValueError(
                f"output {ow}x{oh} exceeds safety limit (100000 per side, 100 million pixels)"
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".crop-", dir=output.parent)
        os.close(fd)
        temp = Path(name)
        try:
            if not any((left, top, right, bottom)):
                shutil.copyfile(source, temp)
            else:
                header = source.open("rb")
                with header:
                    signature = header.read(26)
                depth = signature[24] if fmt == "PNG" else 8
                if fmt == "PNG" and depth == 16:
                    # Pillow truncates multichannel 16-bit PNGs on load.
                    import cv2

                    a = cv2.imdecode(
                        np.frombuffer(source.read_bytes(), dtype=np.uint8), cv2.IMREAD_UNCHANGED
                    )
                    if a is None or a.dtype != np.uint16:
                        raise ValueError("cannot decode PNG while preserving 16-bit samples")
                    b = np.empty((oh, ow, *a.shape[2:]), dtype=a.dtype)
                    gray = round((299 * rgb[0] + 587 * rgb[1] + 114 * rgb[2]) / 1000)
                    color = (
                        gray * 257
                        if a.ndim == 2
                        else tuple(c * 257 for c in rgb[::-1])
                        + ((65535,) if a.shape[2] == 4 else ())
                    )
                    b[...] = color
                    b[
                        max(0, -top) : max(0, -top) + y1 - y0,
                        max(0, -left) : max(0, -left) + x1 - x0,
                    ] = a[y0:y1, x0:x1]
                    ok, encoded = cv2.imencode(".png", b)
                    if not ok:
                        raise ValueError("16-bit PNG encoding failed")
                    temp.write_bytes(encoded.tobytes())
                else:
                    im.load()
                    options = {}
                    if fmt == "PNG" and depth < 8 and im.mode == "L":
                        # Keep low-bit grayscale precision using an equivalent palette.
                        levels = (1 << depth) - 1
                        im = im.point(lambda p: round(p * levels / 255)).convert("P")
                        im.putpalette(
                            [
                                v
                                for i in range(256)
                                for v in (round(min(i, levels) * 255 / levels),) * 3
                            ]
                        )
                    if im.mode == "P":
                        palette = im.getpalette()
                        limit = 1 << depth
                        colors = [
                            tuple(palette[i : i + 3])
                            for i in range(0, min(len(palette), limit * 3), 3)
                        ]
                        transparency = im.info.get("transparency")

                        def opaque(index):
                            if isinstance(transparency, int):
                                return index != transparency
                            return (
                                not isinstance(transparency, bytes)
                                or index >= len(transparency)
                                or transparency[index] == 255
                            )

                        matches = [i for i, c in enumerate(colors) if c == rgb and opaque(i)]
                        if any(p < 0 for p in (left, top, right, bottom)) and not matches:
                            raise ValueError(
                                "pad_color needs an opaque entry in the indexed PNG palette; cannot preserve palette bit depth exactly"
                            )
                        fill = matches[0] if matches else 0
                    else:
                        fill = Image.new("RGB", (1, 1), rgb).convert(im.mode).getpixel((0, 0))
                    result = Image.new(im.mode, (ow, oh), fill)
                    if im.mode == "P":
                        result.putpalette(im.getpalette())
                    result.paste(im.crop((x0, y0, x1, y1)), (max(0, -left), max(0, -top)))
                    for key in ("icc_profile", "exif", "dpi", "transparency"):
                        if key in im.info:
                            options[key] = im.info[key]
                    if fmt == "PNG":
                        options["bits"] = depth
                    elif fmt == "JPEG":
                        options.update(quality=95, subsampling=0)
                    else:
                        options["lossless"] = True
                    result.save(temp, format=fmt, **options)
            os.replace(temp, output)
        finally:
            temp.unlink(missing_ok=True)
    print(
        f"[ITER 1/1] {fmt} {w}x{h} -> {ow}x{oh}; L/T/R/B={left}/{top}/{right}/{bottom}", flush=True
    )
    return ow, oh


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--image",
        required=True,
        type=Path,
        help="PNG/JPEG/WebP; format detected from bytes, including extensionless handles",
    )
    ap.add_argument("--output", required=True, type=Path)
    for side in ("left", "top", "right", "bottom"):
        ap.add_argument(
            "--pad-" + side,
            type=float,
            default=0,
            help="Pixels: positive crops inward, negative pads outward; round half away from zero. Empty original crop is an error.",
        )
    ap.add_argument(
        "--pad-color",
        default="#000000",
        help="Opaque #RRGGBB border, default black; grayscale uses luminance",
    )
    args = ap.parse_args()
    try:
        crop_image(
            args.image,
            args.output,
            (args.pad_left, args.pad_top, args.pad_right, args.pad_bottom),
            args.pad_color,
        )
    except (ValueError, OSError) as exc:
        ap.exit(2, f"image-crop: {exc}\n")


if __name__ == "__main__":
    main()
