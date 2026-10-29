#!/usr/bin/env python3
"""Compare encoders on exactly the same five decoded RGB frames; no decode time."""

import io
import json
import pathlib
import time

import numpy as np
from PIL import Image, features

ROOT = pathlib.Path(__file__).resolve().parent
SOURCE = pathlib.Path("/dev/shm/fe-research-scratch/png_p8_auto/1")
rows = []
for fmt, opts in [
    ("PNG", {"compress_level": 6}),
    ("PNG", {"compress_level": 1}),
    ("JPEG", {"quality": 95, "subsampling": 0}),
    ("JPEG", {"quality": 95, "subsampling": 2}),
    ("WEBP", {"quality": 95, "method": 4}),
    ("WEBP", {"lossless": True, "quality": 75, "method": 0}),
]:
    size = elapsed = sse = differing = maxerr = count = 0
    for idx in [1, 11, 21, 31, 50]:
        with Image.open(SOURCE / f"f_{idx:06d}.png") as src:
            im = src.convert("RGB")
        a = np.asarray(im).astype(np.int16)
        buf = io.BytesIO()
        t = time.monotonic()
        im.save(buf, format=fmt, **opts)
        elapsed += time.monotonic() - t
        size += len(buf.getvalue())
        with Image.open(io.BytesIO(buf.getvalue())) as reread:
            b = np.asarray(reread.convert("RGB")).astype(np.int16)
        err = a.astype(np.int32) - b
        sse += np.square(err, dtype=np.float64).sum()
        count += err.size
        differing += np.count_nonzero(err)
        maxerr = max(maxerr, int(np.abs(err).max()))
    row = dict(
        format=fmt,
        options=opts,
        frames=5,
        encode_s=elapsed,
        MB=size / 1e6,
        PSNR_dB=float(10 * np.log10(255**2 / (sse / count))) if sse else None,
        max_channel_error=maxerr,
        differing_channel_percent=differing / count * 100,
        identical=bool(sse == 0),
    )
    rows.append(row)
    print(json.dumps(row), flush=True)
(ROOT / "formats.json").write_text(
    json.dumps(
        dict(
            pillow=Image.__version__,
            jpeg=features.version("jpg"),
            webp=features.version("webp"),
            rows=rows,
        ),
        indent=2,
    )
)
