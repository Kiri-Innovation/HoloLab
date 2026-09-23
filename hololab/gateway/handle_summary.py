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
import re
import struct
import threading
from pathlib import Path
from typing import Any

from hololab.gateway.handles import Handle
from hololab.gateway.tag_probes import (
    content_dim_count_for,
    internal_count_for,
    probe_content_dims,
)

_IMAGE_SUFFIX_RE = re.compile(r"\.(png|jpe?g|webp|bmp|gif)$", re.IGNORECASE)

# Cap what we read from a single file. Splatv headers are ~74 KiB on
# real STG models; 256 KiB is comfortable overhead and still trivial.
_MAX_HEADER_BYTES = 256 * 1024

# For directory summaries, cap the entries we return so a huge output
# dir doesn't hand back 10k rows.
_MAX_DIR_ENTRIES = 100

# Per-``frames/`` cap for the 2nd-level drill (see _summarize_dir). The
# nested frame_sequence viewer shows at most 3 mini thumbnails per
# element card, so 8 is a comfortable ceiling and keeps the payload
# small even when a dir has thousands of frames.
_FRAMES_DRILL_CAP = 8


def dim_info_for_handle(
    handle: Handle, *, dim_labels: list[str] | None
) -> tuple[list[str] | None, list[int] | None]:
    """Return ``(dim_labels, dim_sizes)`` for one handle.

    Callers pass ``dim_labels`` from the producing port (looked up via
    ``NodeRegistry.get_output_port_spec``). ``dim_sizes`` is measured by
    walking the tree ``len(dim_labels)`` levels using the same helper
    ``summarize_handle`` uses — so ``/api/handles/{id}`` and
    ``/api/handles/{id}/summary`` share one implementation.

    Returns ``(None, None)`` when the producing port has no declared
    dims, or when the handle isn't a dir on disk. Returns
    ``(dim_labels, None)`` when the tree exists but the walk fails
    (ragged layers, permission errors) — the frontend then falls back
    to single-level.
    """

    if not dim_labels:
        return None, None
    if handle.storage != "dir":
        return dim_labels, None
    path = Path(handle.path)
    if not path.is_dir():
        return dim_labels, None
    sizes, _sample = _measure_dim_sizes(path, len(dim_labels), list(handle.tags))
    return dim_labels, sizes


def summarize_handle(handle: Handle, *, depth: int | None = None) -> dict[str, Any]:
    """Return a summary dict for one handle.

    Dispatch order (see :func:`_dispatch_summary`):
        * tag contains ``int`` + storage=file → scalar-int (parse text)
        * ``storage="dir"``            → directory listing
        * tag contains ``splatv``      → splatv header parse
        * suffix ``.mp4/.mov/.mkv``    → video (structural fields only)
        * suffix ``.png/.jpg/.jpeg``   → image (structural fields only)
        * suffix ``.txt/.log/.json``   → text preview (first N lines)
        * everything else              → ``kind="unknown"``, size only

    On top of kind + fields we also try to fill:
        * ``element_count`` — subdir count on dir handles (edge chip ``[N]``).
        * ``dim_sizes`` — per-dim element counts, outer dim first
          (``[100, 21]`` for a 2-D frame x cam handle). Computed only when
          the caller supplies ``depth`` (from the producing port's
          ``dim_labels`` length); walks the tree ``depth`` levels reporting
          the branching factor at each. ``depth=0`` marks the handle scalar
          and always yields ``dim_sizes=null``.
        * ``internal_count`` + ``internal_count_kind`` — tag-specific
          inside-one-element number via :mod:`hololab.gateway.tag_probes`;
          sampled from the FIRST leaf of an arrayed handle.
    """

    path = Path(handle.path)
    base = _dispatch_summary(handle, path)
    _annotate_element_and_internal_counts(base, handle, path, depth=depth)
    return base


def _dispatch_summary(handle: Handle, path: Path) -> dict[str, Any]:
    """Original kind/fields dispatch — split out so annotators layer on top."""

    if "int" in handle.tags and handle.storage == "file":
        return _summarize_int(path, handle)

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


def _annotate_element_and_internal_counts(
    result: dict[str, Any],
    handle: Handle,
    path: Path,
    *,
    depth: int | None = None,
) -> None:
    """Fill ``element_count`` + ``dim_sizes`` + ``internal_count`` + kind.

    ``element_count`` counts immediate non-dot subdirs on dir handles. The
    frontend uses it to render the edge chip's ``[N]``; whether the port
    is *treated* as arrayed is the graph edge's business.

    ``dim_sizes`` (when ``depth`` is supplied) walks ``depth`` levels of
    subdirs, one branching factor per level, outer dim first. It requires
    the tree to be uniform at every level (every leaf-of-level has the
    same subdir count) — non-uniform trees yield ``dim_sizes=null`` rather
    than a lie. ``depth=0`` marks a scalar handle and always yields null.
    When set, ``dim_sizes[0]`` equals ``element_count`` by construction.

    ``internal_count`` samples the FIRST leaf of an arrayed dir handle
    (deterministic sorted order) descended ``depth`` levels, or the
    handle root itself when the handle isn't multi-element. Splatv's
    ``fields.camera_count`` is mirrored up so the wire contract is
    uniform.
    """
    element_count: int | None = None
    sample_path: Path = path
    dim_sizes: list[int] | None = None

    if handle.storage == "dir" and path.is_dir():
        try:
            children = sorted(
                c for c in path.iterdir() if not c.name.startswith(".") and c.is_dir()
            )
            element_count = len(children)
            if children:
                sample_path = children[0]
        except OSError:
            element_count = None

        if depth is not None and depth >= 1:
            dim_sizes, leaf = _measure_dim_sizes(path, depth, list(handle.tags))
            if leaf is not None:
                sample_path = leaf

    fields = result.get("fields") or {}
    if result.get("kind") == "splatv" and isinstance(fields.get("camera_count"), int):
        result["internal_count"] = int(fields["camera_count"])
        result["internal_count_kind"] = "cameras"
    else:
        probe = internal_count_for(list(handle.tags), sample_path)
        if probe is not None:
            if probe.items:
                result["internal_count_items"] = [
                    {"label": it.label, "value": it.value} for it in probe.items
                ]
            else:
                result["internal_count"] = probe.count
                result["internal_count_kind"] = probe.kind

    if element_count:
        result["element_count"] = element_count
    if dim_sizes is not None:
        result["dim_sizes"] = dim_sizes


def _measure_dim_sizes(
    root: Path, depth: int, tags: list[str]
) -> tuple[list[int] | None, Path | None]:
    """Compute ``dim_sizes`` for an arrayed/arrayable-wrapped handle.

    ``depth`` is the total ``len(dim_labels)`` declared on the producing
    port. It is split into two phases:

        dir_depth = depth - content_dim_count_for(tags)
        content_dims = depth - dir_depth   # remainder handled by tag probe

    Phase 1 walks ``dir_depth`` subdir layers, enforcing uniformity at
    every level (a ragged tree yields ``None`` — no averaged lies).
    Phase 2 hands the leaf reached in phase 1 to
    :func:`tag_probes.probe_content_dims` for the tag's intrinsic
    layers (e.g. ``image_sequence`` counts files under ``frames/``).

    Returns ``(sizes, sample_leaf)`` where ``sample_leaf`` is the path
    used for the tag_probes internal_count sniff (the phase-1 leaf, or
    ``None`` when the walk fails).
    """

    tag_content_dims = content_dim_count_for(tags)
    dir_depth = depth - tag_content_dims
    if dir_depth < 0:
        # Malformed: pack declared fewer dims than the tag's intrinsic
        # layers demand. Refuse to guess.
        return None, None

    sizes: list[int] = []
    current: list[Path] = [root]
    sample: Path | None = None
    for _ in range(dir_depth):
        next_layer: list[Path] = []
        per_dir: int | None = None
        for parent in current:
            try:
                kids = sorted(
                    c for c in parent.iterdir() if not c.name.startswith(".") and c.is_dir()
                )
            except OSError:
                return None, None
            if per_dir is None:
                per_dir = len(kids)
            elif per_dir != len(kids):
                return None, None
            next_layer.extend(kids)
        if per_dir is None or per_dir == 0:
            return None, None
        sizes.append(per_dir)
        current = next_layer
        sample = next_layer[0]

    if tag_content_dims > 0:
        probe_root = sample if sample is not None else root
        content = probe_content_dims(tags, probe_root)
        if content is None or len(content) != tag_content_dims or any(n <= 0 for n in content):
            return None, sample
        sizes.extend(content)

    return sizes, sample


# ---------------------------------------------------------------------------
# int scalar (plain-text base-10 integer file)
# ---------------------------------------------------------------------------


def _summarize_int(path: Path, _handle: Handle) -> dict[str, Any]:
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
        # Budget shared across the second level so an arrayed<T> handle
        # with 100 element subdirs can't blow the payload up by dragging
        # in thousands of grandchildren. The video-grid drawer (and any
        # other arrayed-aware viewer) only needs the immediate leaf name
        # inside each element to compose per-tile URLs.
        children_budget = _MAX_DIR_ENTRIES
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
            entry: dict[str, Any] = {
                "name": child.name,
                "is_dir": child.is_dir(),
                "size_bytes": size,
            }
            # One-level descent so an arrayed<T> layout
            # (``<parent>/<element>/<file>``) surfaces the leaf filenames
            # a viewer needs to build per-element URLs, without a
            # dedicated per-subdir endpoint or client-side probes.
            if child.is_dir() and children_budget > 0:
                # Post-migration ``arrayed<image>`` puts image files
                # directly under each element (no ``frames/`` wrapper).
                # An element dir can now hold hundreds of thumbnails, so
                # cap that per-element listing the same way the legacy
                # ``frames/`` drill does — otherwise 21 cams x 100 frames
                # would exhaust the shared budget on the first element.
                # Non-image element dirs (colmap frames, video shards,
                # …) stay uncapped since they only hold a handful of
                # top-level entries.
                drill_cap = min(_FRAMES_DRILL_CAP, children_budget)
                child_children = _list_dir_children(child, children_budget)
                looks_image_heavy = any(
                    not gc.get("is_dir") and _IMAGE_SUFFIX_RE.search(gc.get("name", ""))
                    for gc in child_children
                )
                if looks_image_heavy and len(child_children) > drill_cap:
                    entry["children"] = child_children[:drill_cap]
                    entry["entry_count"] = _count_dir_entries(child)
                else:
                    entry["children"] = child_children
                    if looks_image_heavy:
                        # Set entry_count uniformly so NestedFrameSequence
                        # Preview's badge doesn't need to distinguish
                        # "capped" vs "small" cases on the wire.
                        entry["entry_count"] = _count_dir_entries(child)
                children_budget -= len(entry["children"])
                # Legacy ``frames/`` drill: pre-migration handles wrap
                # images in ``<element>/frames/<image>``. Kept so old
                # artifacts still preview after the flatten migration.
                # NestedFrameSequencePreview already handles both
                # layouts on the frontend.
                if children_budget > 0:
                    for gc in entry["children"]:
                        if gc.get("is_dir") and gc.get("name") == "frames" and children_budget > 0:
                            legacy_cap = min(_FRAMES_DRILL_CAP, children_budget)
                            gc["children"] = _list_dir_children(child / "frames", legacy_cap)
                            children_budget -= len(gc["children"])
                            # ``entry_count`` on the drilled dir carries
                            # the true item count even when the children
                            # list is capped, so the nested viewer's
                            # per-group badge shows the real frame count
                            # (e.g. 100) rather than the drill cap (8).
                            gc["entry_count"] = _count_dir_entries(child / "frames")
            entries.append(entry)
        fields["entry_count"] = total
        fields["total_size_bytes"] = total_size
        fields["entries"] = entries
        fields["truncated"] = total > _MAX_DIR_ENTRIES
    except OSError as exc:
        fields["error"] = f"list failed: {exc}"
    return {"kind": "dir", "fields": fields}


def _count_dir_entries(path: Path) -> int:
    """Return the total number of immediate children in ``path``.

    Cheap (no per-entry stat, unlike :func:`_list_dir_children`). Used
    to annotate drilled directories with their true item count when the
    returned ``children`` list is capped — the nested viewer needs to
    show the real frame count (e.g. 100) on each group card, not the
    drill cap (8). Returns 0 on OSError so a permission-denied subdir
    doesn't fail the whole summary.
    """

    try:
        return sum(1 for _ in path.iterdir())
    except OSError:
        return 0


def _list_dir_children(path: Path, budget: int) -> list[dict[str, Any]]:
    """Return the immediate children of ``path`` up to ``budget`` items.

    Silently returns ``[]`` on OSError (e.g. permission denied) — a
    missing children field on one entry shouldn't fail the whole
    summary. Grandchildren are NOT descended into; this is deliberately
    a one-level peek.
    """

    out: list[dict[str, Any]] = []
    try:
        for grandchild in path.iterdir():
            if len(out) >= budget:
                break
            try:
                gst = grandchild.stat()
                gsize = gst.st_size if grandchild.is_file() else None
            except OSError:
                gsize = None
            out.append(
                {
                    "name": grandchild.name,
                    "is_dir": grandchild.is_dir(),
                    "size_bytes": gsize,
                }
            )
    except OSError:
        return []
    return out


# ---------------------------------------------------------------------------
# Per-handle chip-facts cache
# ---------------------------------------------------------------------------


# Compact fact set consumed by the edge chip and the graph endpoints'
# ``latest_run.output_handles`` payload. Only the numeric probes the chip
# actually reads — no entries[], no header bytes — so cache memory stays
# tiny even on graphs with hundreds of handles.
_CHIP_FACT_KEYS = (
    "element_count",
    "dim_sizes",
    "internal_count",
    "internal_count_kind",
    "internal_count_items",
)


class HandleSummaryCache:
    """Per-handle memoize of the chip-facts view of ``summarize_handle``.

    A ``Handle`` is immutable after registration — the file on disk
    doesn't change and the tags are frozen at ``handle_register`` time —
    so we key strictly on ``handle_id`` with no TTL. The one thing that
    invalidates an entry is a tombstone (``DELETE /api/artifacts/{id}``)
    which pops the row via :meth:`invalidate`.

    Cached shape is the same compact subset the frontend edge chip reads
    (``element_count`` / ``dim_sizes`` / ``internal_count`` /
    ``internal_count_kind`` / ``internal_count_items``) — the fields the
    graph endpoints inline under ``latest_run.output_handles``. We do NOT
    cache the full summary (``entries[]`` for dir listings, header bytes
    for splatv/video) — those are on-demand via ``/api/handles/{id}/summary``
    and would blow the memory budget on graphs with hundreds of handles.

    Thread safety: the cache is read + written from the gateway's async
    event loop, but ``summarize_handle`` may run under a threadpool
    (blocking IO). A ``threading.Lock`` guards the dict so a two-worker
    race doesn't double-set the same key with different values.
    """

    def __init__(self, *, max_entries: int = 4096) -> None:
        self._store: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        # Bounded so a long-lived gateway across thousands of workflow
        # runs doesn't grow the cache without bound. FIFO eviction —
        # LRU would need per-lookup writes and the cache is chip-facts
        # only, so the working set is small and eviction is rare in
        # practice. Motivating cap 4k = ~40 nodes/workflow x ~100
        # active workflows before we start evicting.
        self._max_entries = max_entries

    def get_or_compute(
        self,
        handle: Handle,
        *,
        dim_labels: list[str] | None,
    ) -> dict[str, Any]:
        """Return the cached chip facts, computing + caching on miss.

        ``dim_labels`` is the producing port's declared label list — the
        same value the summary endpoint reads via
        :meth:`NodeRegistry.get_output_port_spec`. Passed through so
        ``dim_sizes`` measurement walks the right depth.
        """

        with self._lock:
            hit = self._store.get(handle.handle_id)
            if hit is not None:
                return hit

        depth = len(dim_labels) if dim_labels else None
        full = summarize_handle(handle, depth=depth)
        facts: dict[str, Any] = {}
        for key in _CHIP_FACT_KEYS:
            if key in full and full[key] is not None:
                facts[key] = full[key]

        with self._lock:
            # Race: another worker may have populated the same key while
            # we were probing. Trust the first writer (identical inputs
            # produce identical outputs by construction) and drop our
            # probe on the floor.
            existing = self._store.get(handle.handle_id)
            if existing is not None:
                return existing
            if len(self._store) >= self._max_entries:
                # Simple FIFO: evict the oldest inserted key. dict
                # preserves insertion order in Python 3.7+.
                oldest = next(iter(self._store))
                self._store.pop(oldest, None)
            self._store[handle.handle_id] = facts
            return facts

    def invalidate(self, handle_id: str) -> None:
        """Drop the cached entry for ``handle_id`` if present.

        Called on artifact tombstone so the next chip render doesn't
        surface counts for a file the operator just deleted. A missing
        key is a no-op — the cache is best-effort, not authoritative.
        """

        with self._lock:
            self._store.pop(handle_id, None)

    def size(self) -> int:
        """Test hook — number of cached entries."""

        with self._lock:
            return len(self._store)

    def clear(self) -> None:
        """Test hook — drop everything."""

        with self._lock:
            self._store.clear()
