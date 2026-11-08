"""Compose type-owned summaries with generic collection and runtime-shape evidence.

Format recognition, header parsing and directory presentation live in scanned
``types/*`` packages. This module only executes their declarations and measures
explicit collection layers. Failed summary hooks yield unmeasured evidence.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from hololab.gateway.handles import Handle
from hololab.gateway.tag_probes import (
    content_dim_count_for,
    internal_count_for,
    probe_content_dims,
)


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
    ``(dim_labels, None)`` when the tree exists but is not rectangular
    (or the walk fails). Structured ``runtime_shape`` evidence separately
    retains exact outer sizes and ragged per-parent bounds.
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


def summarize_handle(
    handle: Handle, *, depth: int | None = None, dim_labels: list[str] | None = None
) -> dict[str, Any]:
    """Return a summary dict for one handle.

    Root summary dispatch follows scanned declarations. Counts are sampled
    from the first collection leaf, with the historical fallback semantics.

    On top of kind + fields we also try to fill:
        * ``element_count`` — collection count on dir handles (edge chip ``[N]``).
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
    _annotate_element_and_internal_counts(base, handle, path, depth=depth, dim_labels=dim_labels)
    return base


def _dispatch_summary(handle: Handle, path: Path) -> dict[str, Any]:
    """Original kind/fields dispatch — split out so annotators layer on top."""

    from hololab.type_registry import registry

    registry.refresh()
    definitions = sorted(
        registry.definitions.values(),
        key=lambda d: d.declaration.get("dispatch", {}).get("order", 5),
    )
    for definition in definitions:
        rule = definition.declaration.get("dispatch")
        if rule is None:
            if (
                not definition.declaration.get("summary")
                or definition.declaration.get("summary_role") == "counts"
            ):
                continue
            rule = {"tags": [definition.name]}
        if rule.get("storage") and rule["storage"] != handle.storage:
            continue
        suffix = path.suffix if rule.get("case_sensitive") else path.suffix.lower()
        if (rule.get("tags") or rule.get("suffixes")) and not (
            set(rule.get("tags", [])).intersection(handle.tags)
            or suffix in rule.get("suffixes", [])
        ):
            continue
        value = definition.summary(path, handle.storage)
        if definition.declaration.get("dispatch") is None and (
            value is None or "kind" not in value
        ):
            continue
        if (
            value is not None
            and isinstance(value.get("fields", {}), dict)
            and isinstance(value.get("kind"), str)
        ):
            count = definition.declaration.get("summary_count", {})
            number = value.get("fields", {}).get(count.get("field"))
            if isinstance(number, int):
                value["internal_count"] = number
                value["internal_count_kind"] = count["kind"]
            return value
        return {"kind": "unknown", "fields": {}}
    return {"kind": "unknown", "fields": {}}


def _annotate_element_and_internal_counts(
    result: dict[str, Any],
    handle: Handle,
    path: Path,
    *,
    depth: int | None = None,
    dim_labels: list[str] | None = None,
) -> None:
    """Fill ``element_count`` + ``dim_sizes`` + ``internal_count`` + kind.

    ``element_count`` counts immediate non-dot subdirs, plus files when the
    first collection layer is also the leaf layer. The
    frontend uses it to render the edge chip's ``[N]``; whether the port
    is *treated* as arrayed is the graph edge's business.

    ``dim_sizes`` (when ``depth`` is supplied) is the exact rectangular shape,
    outer dim first. Non-uniform trees keep this legacy field null rather than
    lying. Structured ``runtime_shape`` still publishes independently measured
    axes and min/max cardinality for ragged layers. ``depth=0`` marks a scalar
    handle and always yields null. When set, ``dim_sizes[0]`` equals
    ``element_count`` by construction.

    ``internal_count`` samples the FIRST leaf of an arrayed dir handle
    (deterministic sorted order) descended ``depth`` levels, or the
    handle root itself when the handle isn't multi-element. Splatv's
    ``fields.camera_count`` is mirrored up so the wire contract is
    uniform.
    """
    element_count: int | None = None
    sample_path: Path = path
    dim_sizes: list[int] | None = None
    axis_ranges: list[tuple[int, int] | None] | None = None
    dimension_leaf: Path | None = None

    if handle.storage == "dir" and path.is_dir():
        try:
            leaf_layer = depth is not None and depth - content_dim_count_for(list(handle.tags)) == 1
            children = sorted(
                c
                for c in path.iterdir()
                if not c.name.startswith(".") and (c.is_dir() or (leaf_layer and c.is_file()))
            )
            element_count = len(children)
            if children:
                sample_path = children[0]
        except OSError:
            element_count = None

        if depth is not None and depth >= 1:
            dim_sizes, leaf, axis_ranges = _measure_dim_shape(path, depth, list(handle.tags))
            dimension_leaf = leaf
            if leaf is not None:
                sample_path = leaf

    root_count = "internal_count" in result
    if not root_count:
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
        if dim_sizes:
            result["element_count"] = dim_sizes[0]

    from hololab.gateway.type_observation import observe_summary

    # Record evidence at the point where sampling is known. Existing numeric
    # fields remain untouched for clients that have not adopted the envelope.
    sample = str(sample_path.relative_to(path)) if sample_path != path else None
    axis_sample = (
        str(dimension_leaf.relative_to(path))
        if dimension_leaf is not None and dimension_leaf != path
        else None
    )
    if root_count:
        sample = None  # The type summary measured the root itself.
    result["runtime_shape"] = observe_summary(
        handle_id=handle.handle_id,
        tags=list(handle.tags),
        facts=result,
        labels=dim_labels,
        content_depth=content_dim_count_for(list(handle.tags)),
        sample=sample,
        axis_sample=axis_sample,
        axis_ranges=axis_ranges,
        accessible=path.exists(),
    ).model_dump()


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
    layers if a tag registers them. Collection leaves may be files or
    directories; intermediate collection layers must be directories.

    Returns ``(sizes, sample_leaf)`` where ``sample_leaf`` is the path
    used for the tag_probes internal_count sniff (the phase-1 leaf, or
    ``None`` when the walk fails).
    """

    sizes, sample, _ranges = _measure_dim_shape(root, depth, tags)
    return sizes, sample


def _measure_dim_shape(
    root: Path, depth: int, tags: list[str]
) -> tuple[
    list[int] | None,
    Path | None,
    list[tuple[int, int] | None] | None,
]:
    """Measure exact rectangular sizes plus honest ragged-axis bounds.

    ``dim_sizes`` remains the legacy exact rectangular shape and is therefore
    null for a ragged tree.  The third return value retains independently
    measured axes: an exact axis has ``(n, n)`` and a ragged axis has the
    complete per-parent ``(min, max)`` cardinality range.  This lets runtime
    evidence report ``group:159, cam:6..7`` without claiming ``[159, 7]``.
    """

    tag_content_dims = content_dim_count_for(tags)
    dir_depth = depth - tag_content_dims
    if dir_depth < 0:
        # Malformed: pack declared fewer dims than the tag's intrinsic
        # layers demand. Refuse to guess.
        return None, None, None

    sizes: list[int] = []
    ranges: list[tuple[int, int] | None] = []
    rectangular = True
    current: list[Path] = [root]
    sample: Path | None = None
    for layer in range(dir_depth):
        next_layer: list[Path] = []
        counts: list[int] = []
        for parent in current:
            try:
                # ``Path.iterdir()`` followed by ``is_dir()`` / ``is_file()``
                # turns a large image tree into one ``stat(2)`` per entry.
                # DirEntry keeps the d_type returned by readdir on normal
                # filesystems, so this is both equivalent and substantially
                # cheaper for a folder handle with thousands of images.
                with os.scandir(parent) as scan:
                    kids = sorted(
                        (entry for entry in scan if not entry.name.startswith(".")),
                        key=lambda entry: entry.name,
                    )
                leaf_layer = layer == dir_depth - 1
                if any(not (entry.is_dir() or (leaf_layer and entry.is_file())) for entry in kids):
                    return None, None, ranges + [None] * (depth - len(ranges))
            except OSError:
                return None, None, ranges + [None] * (depth - len(ranges))
            counts.append(len(kids))
            next_layer.extend(Path(entry.path) for entry in kids)
        if not counts or max(counts) == 0:
            return None, None, ranges + [None] * (depth - len(ranges))
        low, high = min(counts), max(counts)
        ranges.append((low, high))
        if low != high:
            rectangular = False
        sizes.append(low)
        current = next_layer
        sample = next_layer[0]

    if tag_content_dims > 0:
        probe_root = sample if sample is not None else root
        content = probe_content_dims(tags, probe_root)
        if content is None or len(content) != tag_content_dims or any(n <= 0 for n in content):
            return None, sample, ranges + [None] * tag_content_dims
        sizes.extend(content)
        ranges.extend((n, n) for n in content)

    return (sizes if rectangular else None), sample, ranges


# ---------------------------------------------------------------------------
# int scalar (plain-text base-10 integer file)
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
    "runtime_shape",
)


class HandleSummaryCache:
    """Per-handle memoize of the chip-facts view of ``summarize_handle``.

    A ``Handle`` is immutable after registration — the file on disk
    doesn't change and the tags are frozen at ``handle_register`` time —
    so we key on ``(handle_id, dim_labels)`` with no TTL.
    Resolved labels are part of the probe scope, and can arrive after a first
    request without graph context. The one thing that
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
        self._store: dict[tuple[str, tuple[str, ...], str], dict[str, Any]] = {}
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

        from hololab.type_registry import registry

        registry.refresh()
        with self._lock:
            if getattr(self, "_type_generation", None) != registry.generation:
                self._store.clear()
                self._type_generation = registry.generation
        cache_key = (handle.handle_id, tuple(dim_labels or []), registry.generation)
        with self._lock:
            hit = self._store.get(cache_key)
            if hit is not None:
                return hit

        depth = len(dim_labels) if dim_labels else None
        full = summarize_handle(handle, depth=depth, dim_labels=dim_labels)
        facts: dict[str, Any] = {}
        for key in _CHIP_FACT_KEYS:
            if key in full and full[key] is not None:
                facts[key] = full[key]

        with self._lock:
            # Race: another worker may have populated the same key while
            # we were probing. Trust the first writer (identical inputs
            # produce identical outputs by construction) and drop our
            # probe on the floor.
            existing = self._store.get(cache_key)
            if existing is not None:
                return existing
            if len(self._store) >= self._max_entries:
                # Simple FIFO: evict the oldest inserted key. dict
                # preserves insertion order in Python 3.7+.
                oldest = next(iter(self._store))
                self._store.pop(oldest, None)
            self._store[cache_key] = facts
            return facts

    def invalidate(self, handle_id: str) -> None:
        """Drop the cached entry for ``handle_id`` if present.

        Called on artifact tombstone so the next chip render doesn't
        surface counts for a file the operator just deleted. A missing
        key is a no-op — the cache is best-effort, not authoritative.
        """

        with self._lock:
            for key in list(self._store):
                if key[0] == handle_id:
                    self._store.pop(key, None)

    def size(self) -> int:
        """Test hook — number of cached entries."""

        with self._lock:
            return len(self._store)

    def clear(self) -> None:
        """Test hook — drop everything."""

        with self._lock:
            self._store.clear()
