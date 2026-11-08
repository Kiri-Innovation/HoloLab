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


def probe_for_tag(tag: str) -> ProbeFn | None:
    """Return the registered probe for ``tag``, or None."""
    from hololab.type_registry import registry

    definition = registry.get(tag)
    if definition is None:
        return None
    if (
        not definition.declaration.get("summary")
        or definition.declaration.get("summary_role") == "fields"
    ):
        return None

    def probe(path):
        value = definition.summary(path, "dir" if path.is_dir() else "file")
        counts = value.get("counts") if value else None
        if not counts:
            return None
        return ProbeResult(
            count=counts["count"],
            kind=counts["kind"],
            items=tuple(ProbeItem(**item) for item in counts.get("items", [])),
        )

    return probe


def internal_count_for(tags: list[str], sample_path: Path) -> ProbeResult | None:
    """Try each tag on ``tags`` in order; return the first probe hit.

    ``sample_path`` is the concrete directory or file the probe should
    inspect. For an arrayed handle callers pass the first element's
    path (never walk the whole set); for a scalar handle they pass the
    handle's own path.
    """
    for tag in tags:
        fn = probe_for_tag(tag)
        if fn is None:
            continue
        try:
            res = fn(sample_path)
        except Exception:
            continue
        if res is not None:
            return res
    return None


# ---------------------------------------------------------------------------
# Content-dim registry — for tags whose semantic type carries intrinsic
# enumeration layers *inside* an opaque leaf (currently no registered tags).
# Previously image bundles were a
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


def content_dim_count_for(tags: list[str]) -> int:
    """Return the intrinsic content-dim count for the first matching tag.

    Zero when no tag on the handle carries content dims (colmap bundles,
    single-file handles, video-source, …). The summary uses this to split
    ``dim_labels`` between physical dir layers and content probes:

        dir_depth = len(dim_labels) - content_dim_count_for(tags)
    """
    for tag in tags:
        from hololab.type_registry import registry

        definition = registry.get(tag)
        entry = definition.declaration.get("content_dims") if definition else None
        if entry is not None:
            return entry["count"]
    return 0


def probe_content_dims(tags: list[str], sample_path: Path) -> list[int] | None:
    """Run the first matching tag's content-dim probe against ``sample_path``.

    Returns the list of intrinsic-dim sizes (outer-first), or ``None`` if
    the tag isn't registered OR the probe couldn't read the expected
    shape (in which case the caller drops ``dim_sizes`` — no partial
    reports).
    """
    for tag in tags:
        from hololab.type_registry import registry

        definition = registry.get(tag)
        entry = definition.declaration.get("content_dims") if definition else None
        if entry is None:
            continue
        try:
            value = definition.summary(sample_path, "dir" if sample_path.is_dir() else "file")
            return value.get("content_dims") if value else None
        except Exception:
            return None
    return None
