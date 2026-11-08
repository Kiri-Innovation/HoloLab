from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_MAX_DIR_ENTRIES = 100
_FRAMES_DRILL_CAP = 8
_IMAGE_SUFFIX_RE = re.compile(r"\.(png|jpe?g|webp|bmp|gif)$", re.IGNORECASE)


def _summarize_dir(path: Path, _storage: str) -> dict[str, Any]:
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


def summary(path, storage):
    return _summarize_dir(path, storage)
