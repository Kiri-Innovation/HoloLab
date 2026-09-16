"""Server-side handle payload sniffers.

Powers ``GET /api/handles/{id}/summary``. The goal: an agent asks
"what's in this handle?" and gets structured fields (gaussians count,
video duration, image dims, directory listing) *without downloading the
payload*. Kept in its own module so adding a new sniffer (e.g. ply,
pointcloud) is one place to edit.

Every sniffer returns a plain dict of scalar fields — no bytes, no big
arrays. On any parse failure we fall back to ``kind='unknown'`` and
return whatever metadata we do have (size, tags). Agents can then
decide whether to download the file themselves.

We do NOT depend on ffprobe/Pillow being installed at the moment; those
sniffers return best-effort fields based on structured headers we can
parse in pure Python. If those deps are added later, extend
:func:`_summarize_video` and :func:`_summarize_image` accordingly.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

from hololab.gateway.handles import Handle

# Cap what we read from a single file. Splatv headers are ~74 KiB on
# real STG models; 256 KiB is comfortable overhead and still trivial.
_MAX_HEADER_BYTES = 256 * 1024

# For directory summaries, cap the entries we return so a huge output
# dir doesn't hand back 10k rows.
_MAX_DIR_ENTRIES = 100


def summarize_handle(handle: Handle) -> dict[str, Any]:
    """Return a summary dict for one handle.

    Dispatch order:
        * ``storage="dir"``            → directory listing
        * tag contains ``splatv``      → splatv header parse
        * suffix ``.mp4/.mov/.mkv``    → video (structural fields only)
        * suffix ``.png/.jpg/.jpeg``   → image (structural fields only)
        * suffix ``.txt/.log/.json``   → text preview (first N lines)
        * everything else              → ``kind="unknown"``, size only
    """

    path = Path(handle.path)

    if handle.storage == "dir":
        return _summarize_dir(path, handle)

    if "splatv" in handle.tags or path.suffix == ".splatv":
        return _summarize_splatv(path, handle)

    suffix = path.suffix.lower()
    if suffix in (".mp4", ".mov", ".mkv", ".webm"):
        return _summarize_video(path, handle)
    if suffix in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        return _summarize_image(path, handle)
    if suffix in (".txt", ".log", ".json", ".md", ".yaml", ".yml"):
        return _summarize_text(path, handle)

    return {"kind": "unknown", "fields": {}}


# ---------------------------------------------------------------------------
# Splatv (magic 0x674B + JSON header + texture bytes)
# ---------------------------------------------------------------------------


def _summarize_splatv(path: Path, _handle: Handle) -> dict[str, Any]:
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


# ---------------------------------------------------------------------------
# Video (no ffprobe dep — return structural metadata we can get from stat)
# ---------------------------------------------------------------------------


def _summarize_video(path: Path, _handle: Handle) -> dict[str, Any]:
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


# ---------------------------------------------------------------------------
# Image (parse PNG/JPEG header for dimensions)
# ---------------------------------------------------------------------------


def _summarize_image(path: Path, _handle: Handle) -> dict[str, Any]:
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


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------


def _summarize_text(path: Path, _handle: Handle) -> dict[str, Any]:
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


# ---------------------------------------------------------------------------
# Directory listing
# ---------------------------------------------------------------------------


def _summarize_dir(path: Path, _handle: Handle) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if not path.is_dir():
        fields["error"] = f"path is not a directory: {path}"
        return {"kind": "dir", "fields": fields}
    try:
        entries: list[dict[str, Any]] = []
        total = 0
        total_size = 0
        for child in path.iterdir():
            total += 1
            if len(entries) >= _MAX_DIR_ENTRIES:
                continue
            try:
                st = child.stat()
                size = st.st_size if child.is_file() else None
            except OSError:
                size = None
            if size is not None:
                total_size += size
            entries.append(
                {
                    "name": child.name,
                    "is_dir": child.is_dir(),
                    "size_bytes": size,
                }
            )
        fields["entry_count"] = total
        fields["total_size_bytes"] = total_size
        fields["entries"] = entries
        fields["truncated"] = total > _MAX_DIR_ENTRIES
    except OSError as exc:
        fields["error"] = f"list failed: {exc}"
    return {"kind": "dir", "fields": fields}
