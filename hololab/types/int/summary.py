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

def _summarize_int(path: Path, _storage: str) -> dict[str, Any]:
    """Parse a scalar ``int`` handle: a file containing one base-10 integer.

    On parse failure we return the raw text under ``raw`` so the frontend
    can show the actual bytes for debugging rather than silently claiming
    the value is unknown.
    """

    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"kind": "scalar-int", "fields": {"error": f"read failed: {exc}"}}

    stripped = raw.strip()
    try:
        value = int(stripped)
    except ValueError:
        return {
            "kind": "scalar-int",
            "fields": {
                "error": f"not a base-10 integer: {stripped[:64]!r}",
                "raw": stripped[:256],
            },
        }
    return {"kind": "scalar-int", "fields": {"value": value}}


def summary(path, storage):
    return _summarize_int(path, storage)
