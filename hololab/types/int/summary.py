from __future__ import annotations

from pathlib import Path
from typing import Any


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
