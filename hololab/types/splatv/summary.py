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

def _summarize_splatv(path: Path, _storage: str) -> dict[str, Any]:
    try:
        with path.open("rb") as f:
            head = f.read(_MAX_HEADER_BYTES)
    except OSError as exc:
        return {"kind": "splatv", "fields": {"error": f"read failed: {exc}"}}

    if len(head) < 8:
        return {"kind": "splatv", "fields": {"error": "file too small"}}

    magic = struct.unpack_from("<H", head, 0)[0]
    json_len = struct.unpack_from("<I", head, 4)[0]

    fields: dict[str, Any] = {
        "magic_hex": f"0x{magic:04x}",
        "header_json_bytes": json_len,
    }

    if magic != 0x674B:
        fields["error"] = f"unexpected magic 0x{magic:04x} (expected 0x674b)"
        return {"kind": "splatv", "fields": fields}

    if 8 + json_len > len(head):
        fields["error"] = "header extends past probe window"
        return {"kind": "splatv", "fields": fields}

    try:
        meta = json.loads(head[8 : 8 + json_len].decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        fields["error"] = f"header JSON parse: {exc}"
        return {"kind": "splatv", "fields": fields}

    first = meta[0] if isinstance(meta, list) and meta else meta if isinstance(meta, dict) else None
    if isinstance(first, dict):
        texw = first.get("texwidth")
        size = first.get("size")
        cameras = first.get("cameras")
        # Stride-4 pixels per gaussian in the lite variant.
        if isinstance(texw, int) and isinstance(size, int) and texw > 0:
            fields["texture_width"] = texw
            fields["texture_height"] = size // (texw * 4)
            fields["gaussian_count"] = size // 16
        if isinstance(cameras, list):
            fields["camera_count"] = len(cameras)
        if "type" in first:
            fields["type"] = first["type"]

    return {"kind": "splatv", "fields": fields}


def summary(path, storage):
    return _summarize_splatv(path, storage)
