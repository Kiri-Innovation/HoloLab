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

def _summarize_text(path: Path, _storage: str) -> dict[str, Any]:
    fields: dict[str, Any] = {"format": path.suffix.lstrip(".").lower()}
    try:
        stat = path.stat()
        fields["size_bytes"] = stat.st_size
        with path.open("rb") as f:
            head = f.read(min(_MAX_HEADER_BYTES, 8 * 1024))
    except OSError as exc:
        return {"kind": "text", "fields": {"error": f"read failed: {exc}"}}
    try:
        text = head.decode("utf-8")
    except UnicodeDecodeError:
        text = head.decode("utf-8", errors="replace")
    lines = text.splitlines()
    fields["line_count_preview"] = len(lines)
    fields["first_lines"] = lines[:20]
    if fields["size_bytes"] and fields["size_bytes"] > len(head):
        fields["truncated"] = True
    return {"kind": "text", "fields": fields}


def summary(path, storage):
    return _summarize_text(path, storage)
