"""Guard the arrayed-partial-preview wiring in the built bundle.

While a fan-out is in progress, an arrayed<T> node's parent aggregate
handle doesn't exist yet — only the completed shards do. The drawer
switches to *partial mode* in that window: it feeds the paginator with
per-shard proxy URLs (each shard's ``output_handles.<port>`` handle
already points at ``<parent_ws>/<port>/<element_id>/`` — the exact URL
the scalar viewer expects) and displays ``i / expected_shards``.

Before this wiring, shard job WS updates were overwriting the aggregate
slot with a single shard's handle, so the paginator opened that element
dir and treated its internal subdirs (``images`` / ``sparse``) as
"elements" — the "images 1/2 + HTTP 404 images.txt" bug.

Bundle-inspection pattern — no vitest infra; the paginator drops
``data-hl-arrayed-partial`` + ``data-hl-arrayed-expected`` attrs into
its outer div and takes a ``partial`` prop, all of which must survive
minification. Skips gracefully when the dist isn't built.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _find_dist_js() -> Path | None:
    dist = (
        Path(__file__).resolve().parent.parent
        / "hololab"
        / "frontend"
        / "dist"
        / "assets"
    )
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


def test_arrayed_paginator_partial_attr(bundle_js: str) -> None:
    """The paginator marks partial mode with ``data-hl-arrayed-partial``."""
    assert "data-hl-arrayed-partial" in bundle_js


def test_arrayed_paginator_expected_attr(bundle_js: str) -> None:
    """The paginator emits ``data-hl-arrayed-expected`` = parent's
    ``expected_shards`` so E2E hooks can assert on the planned total
    without introspecting React state."""
    assert "data-hl-arrayed-expected" in bundle_js


def test_partial_badge_copy(bundle_js: str) -> None:
    """A ``(partial)`` marker + ``partial`` chip both appear so the
    operator visibly knows they're looking at an in-progress fanout."""
    assert "(partial)" in bundle_js
    # The chip in the drawer header
    assert '"partial"' in bundle_js or "'partial'" in bundle_js


def test_partial_fanout_tooltip(bundle_js: str) -> None:
    """The chip's tooltip explains the state so a hover clarifies without
    the operator having to guess what "partial" means."""
    assert "fanout in progress" in bundle_js
