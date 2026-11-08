from __future__ import annotations
import json
import re
import struct
from pathlib import Path
from typing import Any

_MAX_HEADER_BYTES = 256 * 1024
_MAX_DIR_ENTRIES = 100
_FRAMES_DRILL_CAP = 8
_IMAGE_SUFFIX_RE = re.compile(r"\.(png|jpe?g|webp|bmp|gif)$", re.IGNORECASE)

def _summarize_image(path: Path, _storage: str) -> dict[str, Any]:
    fields: dict[str, Any] = {"format": path.suffix.lstrip(".").lower()}
    try:
        with path.open("rb") as f:
            head = f.read(64)
        stat = path.stat()
        fields["size_bytes"] = stat.st_size
    except OSError as exc:
        return {"kind": "image", "fields": {"error": f"read failed: {exc}"}}

    if head.startswith(b"\x89PNG\r\n\x1a\n") and len(head) >= 24:
        # IHDR is always at offset 16 for a valid PNG.
        width, height = struct.unpack_from(">II", head, 16)
        fields["width"] = width
        fields["height"] = height
    elif head.startswith(b"\xff\xd8"):
        # JPEG dims are inside SOFn markers — walk the file cheaply.
        dims = _jpeg_dimensions(path)
        if dims is not None:
            fields["width"], fields["height"] = dims
    # Other formats (webp, gif) left without dims; note that.
    if "width" not in fields:
        fields["note"] = "dimensions not parsed for this format"
    return {"kind": "image", "fields": fields}


def _jpeg_dimensions(path: Path) -> tuple[int, int] | None:
    """Best-effort JPEG width/height by walking Start-Of-Frame markers.

    Doesn't decode pixels — just scans marker chain for the first SOFn
    (0xC0..0xC3, 0xC5..0xC7, 0xC9..0xCB, 0xCD..0xCF).
    """

    try:
        with path.open("rb") as f:
            f.read(2)  # skip SOI
            while True:
                b = f.read(1)
                if not b:
                    return None
                if b != b"\xff":
                    continue
                # skip fill bytes
                while b == b"\xff":
                    b = f.read(1)
                    if not b:
                        return None
                marker = b[0]
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    f.read(2)  # length
                    f.read(1)  # precision
                    hb = f.read(2)
                    wb = f.read(2)
                    if len(hb) < 2 or len(wb) < 2:
                        return None
                    return (struct.unpack(">H", wb)[0], struct.unpack(">H", hb)[0])
                # skip this segment
                length_bytes = f.read(2)
                if len(length_bytes) < 2:
                    return None
                length = struct.unpack(">H", length_bytes)[0]
                if length < 2:
                    return None
                f.read(length - 2)
    except OSError:
        return None


def summary(path, storage):
    return _summarize_image(path, storage)
