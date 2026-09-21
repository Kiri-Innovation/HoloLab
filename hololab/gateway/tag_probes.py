"""Tag → internal-count probe registry.

Handle summaries carry two "how many" numbers on the wire:

* ``element_count`` — top-level shard count for an arrayed handle
  (``arrayed<T>`` = dir whose immediate subdirs are elements). Computed
  by walking the handle's root once.
* ``internal_count_items`` — a tag-specific list of labeled values, one
  per meaningful metric inside one element (e.g. ``colmap`` → ``cam:21``
  + ``point:6685``). Supersedes the older scalar ``internal_count`` /
  ``internal_count_kind`` pair (still emitted for splatv and any tag
  that hasn't migrated yet).

This module is the registry that maps tag names to probe callables.
Sibling of :mod:`hololab.gateway.tag_viewers` (which maps tags to
viewers) — same "lookup by tag" pattern, but for a different UI concern.

Probes MUST be cheap. A probe running against a live production handle
should not open more than a couple of files. When in doubt, sample the
first element of an arrayed handle only; never walk N elements.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProbeItem:
    """One labeled value within a multi-item probe result.

    The chip renders ``label:value``; the frontend suppresses items
    whose value is 0 so empty categories stay invisible.
    """

    label: str
    value: int


@dataclass(frozen=True)
class ProbeResult:
    """One probe's answer.

    ``kind`` is the noun the tooltip uses ("7 cameras", "1024 points").
    When ``items`` is non-empty the chip renders ``(label:value …)`` for
    each item with value ≠ 0; ``count`` / ``kind`` are kept for backward
    compatibility with callers that predate the multi-item upgrade.
    """

    count: int
    kind: str
    items: tuple[ProbeItem, ...] = ()


ProbeFn = Callable[[Path], ProbeResult | None]


def _parse_colmap_header_count(txt: Path, keyword: str) -> int | None:
    """Parse ``# <keyword> N`` from a COLMAP text file header.

    COLMAP always writes a comment like::

        # Number of images: 21, mean observations per image: 1912.4
        # Number of cameras: 3
        # Number of points: 6685, mean track length: 5.7

    Returns the integer N, or None if not found or file unreadable.
    Stops reading after the header block (first non-comment non-blank line).
    """
    if not txt.is_file():
        return None
    try:
        with txt.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                if not s.startswith("#"):
                    break
                if keyword in s:
                    try:
                        part = s.split(keyword)[1].split(",")[0].strip()
                        return int(part)
                    except (ValueError, IndexError):
                        pass
    except OSError:
        return None
    return None


def _probe_colmap_cameras_txt(path: Path) -> ProbeResult | None:
    """Count camera model rows in ``cameras.txt``.

    COLMAP's cameras.txt has one row per distinct camera *model* (not per
    physical camera). After image-undistort this is typically 1 (shared
    PINHOLE model for all cameras). ``label="model"`` disambiguates from
    view count so the chip reads ``model:1``, not ``cam:1``.
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
    return ProbeResult(count=count, kind="cam models", items=(ProbeItem("model", count),))


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
    return ProbeResult(count=count, kind="points", items=(ProbeItem("point", count),))


def _probe_colmap_images_txt(path: Path) -> ProbeResult | None:
    """Count image views in ``images.txt`` via COLMAP header comment.

    ``images.txt`` always opens with ``# Number of images: N`` which is the
    authoritative count regardless of whether observation lines are blank
    (colmap-split output) or populated (sfm output).
    """
    txt = path / "images.txt" if path.is_dir() else path
    n = _parse_colmap_header_count(txt, "Number of images:")
    if n is not None:
        return ProbeResult(count=n, kind="views", items=(ProbeItem("view", n),))
    return None


def _probe_colmap_cams(path: Path) -> ProbeResult | None:
    """Count calibrated views in a colmap-cams bundle (cameras + images).

    The sfm-cams-only output dir contains ``cameras.txt`` (intrinsics,
    often 1 shared model) and ``images.txt`` (one pose per view). The
    view count from ``images.txt`` is the most useful number — it tells
    how many physical cameras were calibrated. ``label="cam"`` conveys
    "physical cameras" rather than "camera models".
    """
    txt = path / "images.txt" if path.is_dir() else path
    n = _parse_colmap_header_count(txt, "Number of images:")
    if n is not None:
        return ProbeResult(count=n, kind="views", items=(ProbeItem("cam", n),))
    return None


def _probe_colmap(path: Path) -> ProbeResult | None:
    """Dual-metric probe for a per-frame COLMAP reconstruction directory.

    ``colmap-triangulate`` outputs ``sparse/0/{cameras,images,points3D}.txt``.
    Returns two labeled items when both files are readable:

    * ``cam:N``   — view count from ``images.txt`` header (physical cameras)
    * ``point:M`` — 3-D point count from ``points3D.txt`` header

    Items with value 0 are included so the frontend can decide whether to
    suppress them (it always hides value=0 items by convention).
    Returns None only when neither file is found.
    """
    sparse0 = path / "sparse" / "0"
    recon_root = sparse0 if sparse0.is_dir() else path

    cam_n = _parse_colmap_header_count(recon_root / "images.txt", "Number of images:")

    point_n: int | None = None
    for candidate in (
        path / "sparse" / "0" / "points3D.txt",
        path / "points3D.txt",
    ):
        n = _parse_colmap_header_count(candidate, "Number of points:")
        if n is not None:
            point_n = n
            break

    if cam_n is None and point_n is None:
        return None

    items: list[ProbeItem] = []
    if cam_n is not None:
        items.append(ProbeItem("cam", cam_n))
    if point_n is not None:
        items.append(ProbeItem("point", point_n))

    first_val = cam_n if cam_n is not None else point_n
    assert first_val is not None
    return ProbeResult(count=first_val, kind="points", items=tuple(items))


# Tag → probe function. First match wins when a handle carries multiple
# tags. Keep tags here in the same casing they appear in manifests.
_REGISTRY: dict[str, ProbeFn] = {
    "colmap-cameras-txt": _probe_colmap_cameras_txt,
    "colmap-points-txt": _probe_colmap_points_txt,
    "colmap-images-txt": _probe_colmap_images_txt,
    "colmap-cams": _probe_colmap_cams,
    "colmap": _probe_colmap,
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
    """One intrinsic dim: count image files inside the element leaf.

    New layout (post frames/ removal): the element leaf IS the sequence —
    image files live directly under ``<leaf>/`` (``<leaf>/frame_XXXXXX.png``
    from frame-extraction, or ``<leaf>/cam_YYYY.png`` from the
    ``arrayed<arrayed<image>>`` transpose). Legacy layout kept the images
    one level down under ``<leaf>/frames/`` — still probed as a fallback
    so pre-migration handles keep reporting ``dim_sizes`` correctly.
    """
    try:
        direct = sum(1 for c in path.iterdir() if not c.name.startswith(".") and c.is_file())
    except OSError:
        return None
    if direct > 0:
        return [direct]
    frames = path / "frames"
    if not frames.is_dir():
        return None
    try:
        n = sum(1 for c in frames.iterdir() if not c.name.startswith(".") and c.is_file())
    except OSError:
        return None
    if n == 0:
        return None
    return [n]


# tag → (content_dim_count, probe_fn). Content dim count = number of
# intrinsic layers the tag adds on top of whatever dir depth the port's
# arrayed/wrap declaration produces.
_CONTENT_DIM_REGISTRY: dict[str, tuple[int, ContentDimProbeFn]] = {
    "image": (1, _probe_image_sequence_content_dims),
    # Aliases retained for backward compat with pre-migration handles.
    "image_sequence": (1, _probe_image_sequence_content_dims),
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
