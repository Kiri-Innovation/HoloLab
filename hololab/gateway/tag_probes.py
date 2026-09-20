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


# ---------------------------------------------------------------------------
# Content-dim registry — for tags whose semantic type carries intrinsic
# enumeration layers *inside* the leaf (e.g. ``image_sequence`` is a
# sequence of files under ``frames/`` — one intrinsic dim beyond whatever
# arrayed wrapping the port has). Consumed by
# ``handle_summary._measure_dim_sizes`` to compute ``dim_sizes[k:]`` for
# labels beyond the physical dir depth.
#
# Contract: a content-dim probe takes the concrete leaf path (after the
# walker has descended the physical dir layers) and returns
# ``list[int]`` — the sizes of the intrinsic content dims in the same
# outer-first order the port's ``dim_labels`` declares them.
# ``None`` means "probe failed / not applicable"; the annotator drops
# ``dim_sizes`` entirely in that case (never emits a partial lie).
# ---------------------------------------------------------------------------


ContentDimProbeFn = Callable[[Path], list[int] | None]


def _probe_image_sequence_content_dims(path: Path) -> list[int] | None:
    """One intrinsic dim: count image files under ``<leaf>/frames/``.

    ``image_sequence`` is by convention ``<leaf>/frames/frame_XXXXXX.png``
    (post-``regroup-by-frame`` the leaf-inside files are per-camera
    ``cam_YY.png`` — same layer, different naming).
    """
    frames = path / "frames"
    if not frames.is_dir():
        return None
    try:
        n = sum(1 for c in frames.iterdir() if not c.name.startswith(".") and c.is_file())
    except OSError:
        return None
    return [n]


# tag → (content_dim_count, probe_fn). Content dim count = number of
# intrinsic layers the tag adds on top of whatever dir depth the port's
# arrayed/wrap declaration produces.
_CONTENT_DIM_REGISTRY: dict[str, tuple[int, ContentDimProbeFn]] = {
    "image_sequence": (1, _probe_image_sequence_content_dims),
    # ``frame_sequence`` alias kept alongside so pre-migration handles
    # still get inner-dim probing without needing to canonicalize tags
    # at the summary layer.
    "frame_sequence": (1, _probe_image_sequence_content_dims),
}


def content_dim_count_for(tags: list[str]) -> int:
    """Return the intrinsic content-dim count for the first matching tag.

    Zero when no tag on the handle carries content dims (colmap bundles,
    single-file handles, video-source, …). The summary uses this to split
    ``dim_labels`` between physical dir layers and content probes:

        dir_depth = len(dim_labels) - content_dim_count_for(tags)
    """
    for tag in tags:
        entry = _CONTENT_DIM_REGISTRY.get(tag)
        if entry is not None:
            return entry[0]
    return 0


def probe_content_dims(tags: list[str], sample_path: Path) -> list[int] | None:
    """Run the first matching tag's content-dim probe against ``sample_path``.

    Returns the list of intrinsic-dim sizes (outer-first), or ``None`` if
    the tag isn't registered OR the probe couldn't read the expected
    shape (in which case the caller drops ``dim_sizes`` — no partial
    reports).
    """
    for tag in tags:
        entry = _CONTENT_DIM_REGISTRY.get(tag)
        if entry is None:
            continue
        _n, probe = entry
        try:
            return probe(sample_path)
        except OSError:
            return None
    return None
