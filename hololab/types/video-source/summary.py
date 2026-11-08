from __future__ import annotations

from pathlib import Path
from typing import Any


def _summarize_video(path: Path, _storage: str) -> dict[str, Any]:
    fields: dict[str, Any] = {"container": path.suffix.lstrip(".").lower()}
    try:
        stat = path.stat()
        fields["size_bytes"] = stat.st_size
        fields["mtime"] = stat.st_mtime
    except OSError as exc:
        fields["error"] = f"stat failed: {exc}"
    # We deliberately do NOT try to parse container headers here — that
    # varies wildly by codec and needs ffprobe. Agents that need frame
    # counts / duration should download and probe locally, or we can add
    # a proper ffprobe wrapper in a follow-up.
    fields["note"] = (
        "duration / resolution require ffprobe (not currently invoked); "
        "download via proxy_url if you need them"
    )
    return {"kind": "video", "fields": fields}


def summary(path, storage):
    return _summarize_video(path, storage)
