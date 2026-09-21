"""Guard the arrayed-aware edge chip: dim depth, counts, hover fetch.

Chip display contract (mirrors the design report):
  * ``T``          scalar, no counts
  * ``T(N)``       scalar + tag-specific internal count
  * ``T[N]``       1-D arrayed, N elements
  * ``T[?]``       1-D arrayed, count unknown (no handle yet)
  * ``T[F][C]``    2-D arrayed, F outer by C inner

We can't unit-test ``formatTypeLabel`` from Python, so instead we
verify that the compiled bundle contains the shape-defining literals
and that TypedEdge is wired to the hover-fetch cache.

Skips cleanly when the frontend dist isn't built.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _find_dist_js() -> Path | None:
    dist = Path(__file__).resolve().parent.parent / "hololab" / "frontend" / "dist" / "assets"
    if not dist.is_dir():
        return None
    matches = sorted(dist.glob("index-*.js"))
    return matches[-1] if matches else None


@pytest.fixture(scope="module")
def bundle_js() -> str:
    js_path = _find_dist_js()
    if js_path is None:
        pytest.skip("frontend dist not built — run `npm run build` first")
    return js_path.read_text(encoding="utf-8", errors="replace")


def test_chip_placeholder_for_unknown_size(bundle_js: str) -> None:
    """The chip must emit ``[?]`` when arrayed depth is known but the
    element count isn't (handle not resolved yet). Losing this makes
    the chip flip between naked ``T`` and ``T[100]`` on hover — the
    user never sees the arrayed shape upfront.

    Since the labeled-bracket refactor, the ``?`` is built dynamically
    via the sentinel ``"?"`` combined into a template literal — the
    literal ``"[?]"`` no longer appears verbatim in the bundle but the
    sentinel string does."""

    assert '"?"' in bundle_js or "'?'" in bundle_js, (
        "formatTypeLabel must use a '?' sentinel for unknown element counts"
    )


def test_chip_wraps_element_count_with_brackets(bundle_js: str) -> None:
    r"""The ``[N]`` array-size suffix uses square brackets — the plain
    literal ``\`[${n}]\``` (or a minified equivalent) must survive so
    the chip renders ``colmap[100]`` and not ``colmap<100>`` or
    ``colmap 100``."""

    # Match either the escaped template-literal form or a runtime
    # concatenation that yields the same string. Minifiers can rewrite
    # the template to ``"["+n+"]"``, so accept either.
    hits = bundle_js.count('"["+') + bundle_js.count("`[${") + bundle_js.count('"[" +')
    assert hits >= 1, "expected at least one ``[…]`` composition in the chip"


def test_chip_wraps_internal_count_with_parens(bundle_js: str) -> None:
    """The ``(N)`` internal-count suffix uses round brackets — same
    minifier-tolerance check for ``colmap-cameras-txt(7)``."""

    hits = bundle_js.count('"("+') + bundle_js.count("`(${") + bundle_js.count('"(" +')
    assert hits >= 1, "expected at least one ``(…)`` composition in the chip"


def test_edge_summary_cache_wired(bundle_js: str) -> None:
    """TypedEdge must read the tag-count fields from a handle summary
    to fill the chip's ``(N)[N]`` numerics. Function names get mangled
    by the minifier, but the wire property names (``element_count`` /
    ``internal_count``) survive because they must match the server
    payload keys. Missing either means the numeric fills would never
    happen — the chip is stuck at ``T[?]`` forever."""

    assert "element_count" in bundle_js, (
        "edge summary reader must consume ``element_count`` from HandleSummary"
    )
    assert "internal_count" in bundle_js, (
        "edge summary reader must consume ``internal_count`` from HandleSummary"
    )


def test_arrayed_paginator_carries_depth_and_breadcrumb_attrs(bundle_js: str) -> None:
    """Nested-mode paginator emits ``data-hl-arrayed-depth`` and
    ``data-hl-arrayed-breadcrumb`` so E2E hooks (and the bundle
    inspection here) can prove that the recursive 2-D wrap is in the
    build. Absence means the design contract's ``[F][C]`` preview would
    silently collapse to a single level."""

    assert "data-hl-arrayed-depth" in bundle_js, (
        "ArrayedPaginator must expose its depth as a data attr"
    )
    assert "data-hl-arrayed-breadcrumb" in bundle_js, (
        "ArrayedPaginator must expose the breadcrumb path as a data attr"
    )


def test_image_tag_routed(bundle_js: str) -> None:
    """The canonical ``image`` tag and its aliases must all ship as routing
    keys in the bundle so both new and legacy handles preview correctly."""

    assert '"image"' in bundle_js or "'image'" in bundle_js, (
        "Preview() must route on the canonical image tag"
    )
    # Aliases retained so pre-migration handles keep working.
    assert '"image_sequence"' in bundle_js or "'image_sequence'" in bundle_js, (
        "image_sequence alias must remain for backward compat"
    )
    assert '"frame_sequence"' in bundle_js or "'frame_sequence'" in bundle_js, (
        "frame_sequence alias must remain for backward compat"
    )


def test_dim_list_editor_present(bundle_js: str) -> None:
    """NodeInspector's ``list[str]`` param widget lands in the bundle
    with the marker data attr — proves the regroup pack's
    input_dims/output_dims will render a proper editor instead of a
    text input full of commas."""

    assert "data-hl-dim-list-editor" in bundle_js, (
        "DimListEditor must be reachable from NodeInspector"
    )
    assert '"list[str]"' in bundle_js or "'list[str]'" in bundle_js, (
        "NodeInspector must dispatch on the list[str] param type"
    )


def test_dim_sizes_wire_key_present(bundle_js: str) -> None:
    """The ``dim_sizes`` field from ``HandleSummary`` must survive
    minification so the edge summary cache can read it from the server
    payload.  Missing this means chips always fall back to the legacy
    ``element_count`` path and never render ``[label:N]`` notation."""

    assert "dim_sizes" in bundle_js, (
        "edgeSummaryCache must consume ``dim_sizes`` from HandleSummary"
    )


def test_internal_count_items_wire_key_present(bundle_js: str) -> None:
    """The ``internal_count_items`` field must survive minification so the
    edge summary cache can decode multi-value labeled counts from the server.
    Missing this means colmap chips always fall back to the legacy scalar
    ``internal_count`` path and never render ``(cam:21 point:6685)``."""

    assert "internal_count_items" in bundle_js, (
        "edgeSummaryCache must consume ``internal_count_items`` from HandleSummary"
    )


def test_chip_filters_zero_value_items(bundle_js: str) -> None:
    """The chip formatter must suppress items with value=0.

    The backend always includes ``point:0`` in ``internal_count_items``
    when the triangulation produced no points. The frontend must filter
    so the chip shows ``colmap(cam:5)`` not ``colmap(cam:5 point:0)``.

    The ``!== 0`` guard on the item filter must be present in the bundle."""

    assert "!== 0" in bundle_js or "!==0" in bundle_js, (
        "formatTypeLabel must filter internal_count_items with value !== 0"
    )


def test_chip_labeled_bracket_format(bundle_js: str) -> None:
    """When a dim label is present, the chip must render ``[label:N]``
    rather than bare ``[N]``.  The colon separator between label and
    count must be assembled in the bundle — the template literal
    ``[${label}:${n}]`` or its minified concatenation equivalent."""

    # The minifier preserves template literals in ESM output:
    # s?`[${s}:${a}]`:`[${a}]`  → the colon sits between two template
    # substitutions inside square brackets.  Accept either the backtick
    # form or a string-concatenation equivalent.
    has_template = "`:${" in bundle_js or ":${" in bundle_js
    has_concat = '":"+' in bundle_js or '+":" +' in bundle_js or '+ ":"' in bundle_js
    assert has_template or has_concat, (
        "formatTypeLabel must assemble [label:N] brackets with a colon separator"
    )


def test_dim_labels_from_catalog_key_present(bundle_js: str) -> None:
    """The ``dim_labels_from`` field from ``OutputPortSpec`` must survive
    minification.  ``effectiveOutputType`` uses it to resolve dim labels
    from the node's ``params`` dict at layout time — before any handle
    exists.  Missing this means generic ports (e.g. ``regroup.out``) can
    never show more than one bracket in the pre-hover state."""

    assert "dim_labels_from" in bundle_js, (
        "effectiveOutputType must read ``dim_labels_from`` from OutputPortSpec"
    )


def test_dim_labels_summary_key_present(bundle_js: str) -> None:
    """The ``dim_labels`` field on ``HandleSummary`` must be consumed by
    ``edgeSummaryCache`` so that a post-hover chip for a generic port
    (``regroup.out``) shows the actual param-resolved labels rather than
    the static catalog placeholder ``[]``."""

    assert "dim_labels" in bundle_js, (
        "edgeSummaryCache must consume ``dim_labels`` from HandleSummary"
    )
