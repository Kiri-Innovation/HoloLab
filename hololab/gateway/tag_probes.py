"""Tag → internal-count probe registry.

Handle summaries carry two "how many" numbers on the wire:

* ``element_count`` — top-level shard count for an arrayed handle
  (``arrayed<T>`` = dir whose immediate subdirs are elements). Computed
  by walking the handle's root once.
* ``internal_count`` — a tag-specific *inside-one-element* count. What
  it means depends on the tag: ``colmap-cameras-txt`` → cameras.txt row
  count; ``colmap-points-txt`` → points3D.txt row count; ``splatv`` →
  the ``camera_count`` already parsed out of the header.

This module is the registry that maps tag names to probe callables and
their ``internal_count_kind`` label ("cameras", "points", …). Sibling
of :mod:`hololab.gateway.tag_viewers` (which maps tags to viewers) — same
"lookup by tag" pattern, but for a different UI concern.

Probes MUST be cheap. A probe running against a live production handle
should not open more than a couple of files. When in doubt, sample the
first element of an arrayed handle only; never walk N elements.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProbeResult:
    """One probe's answer: ``(count, kind)``.

    ``kind`` is the noun the tooltip uses ("7 cameras", "1024 points").
    """

    count: int
    kind: str


ProbeFn = Callable[[Path], ProbeResult | None]


def _probe_colmap_cameras_txt(path: Path) -> ProbeResult | None:
    """Count camera rows in ``cameras.txt``.

    COLMAP's cameras.txt is one camera per non-comment non-blank line.
    Path can be either the file itself or a directory containing it
    (element root convention: ``<element>/cameras.txt``).
    """
    txt = path / "cameras.txt" if path.is_dir() else path
    if not txt.is_file():
        return None
    try:
        count = 0
        with txt.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                count += 1
    except OSError:
        return None
    return ProbeResult(count=count, kind="cameras")


def _probe_colmap_points_txt(path: Path) -> ProbeResult | None:
    """Count point rows in ``points3D.txt`` (one point per data line)."""
    txt = path / "points3D.txt" if path.is_dir() else path
    if not txt.is_file():
        return None
    try:
        count = 0
        with txt.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                count += 1
    except OSError:
        return None
    return ProbeResult(count=count, kind="points")


# Tag → probe function. First match wins when a handle carries multiple
# tags. Keep tags here in the same casing they appear in manifests.
_REGISTRY: dict[str, ProbeFn] = {
    "colmap-cameras-txt": _probe_colmap_cameras_txt,
    "colmap-points-txt": _probe_colmap_points_txt,
}


def probe_for_tag(tag: str) -> ProbeFn | None:
    """Return the registered probe for ``tag``, or None."""
    return _REGISTRY.get(tag)


def internal_count_for(tags: list[str], sample_path: Path) -> ProbeResult | None:
    """Try each tag on ``tags`` in order; return the first probe hit.

    ``sample_path`` is the concrete directory or file the probe should
    inspect. For an arrayed handle callers pass the first element's
    path (never walk the whole set); for a scalar handle they pass the
    handle's own path.
    """
    for tag in tags:
        fn = _REGISTRY.get(tag)
        if fn is None:
            continue
        try:
            res = fn(sample_path)
        except OSError:
            continue
        if res is not None:
            return res
    return None
