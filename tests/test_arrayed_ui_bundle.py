"""Guard the arrayed<T> UI surface: Inspector checkbox, node frame, port visuals.

When a pack is ``arrayable``:
  * NodeInspector renders a ``data-hl-arrayed-toggle`` checkbox with the
    Chinese copy 「并行处理数组输入」 and a hint block that mentions the
    ``{parent}/{port}/{element_id}/`` layout convention.
  * AlgorithmNode wraps arrayed graph nodes in an outer frame carrying a
    ``data-hl-arrayed-band`` top row that reads 「arrayed」. Input/output
    handles ride the frame's outer border rather than the inner card's.
  * Ports whose effective ``arrayed`` state is true carry
    ``data-hl-arrayed-port`` so E2E hooks + custom styling can select
    them without inferring from ReactFlow internals.

Bundle-inspection pattern — no vitest infra; we grep the built dist for
copy + data-attributes the browser will see. Skips gracefully when the
dist isn't built.
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


# ---------------------------------------------------------------------------
# Inspector checkbox
# ---------------------------------------------------------------------------


def test_inspector_checkbox_data_attr(bundle_js: str) -> None:
    """NodeInspector emits a ``data-hl-arrayed-toggle`` checkbox."""
    assert "data-hl-arrayed-toggle" in bundle_js


def test_inspector_checkbox_label(bundle_js: str) -> None:
    """Checkbox label: 「并行处理数组输入」."""
    assert "并行处理数组输入" in bundle_js


def test_inspector_hint_mentions_element_id_layout(bundle_js: str) -> None:
    """Hint calls out the fan-out disk layout so operators know the shape."""
    assert "element_id" in bundle_js


# ---------------------------------------------------------------------------
# AlgorithmNode arrayed frame
# ---------------------------------------------------------------------------


def test_arrayed_frame_band_data_attr(bundle_js: str) -> None:
    """Arrayed nodes wear an outer frame whose top band carries the marker.

    The redesign dropped the in-header 「(arrayed)」 suffix in favour of a
    full-width top band above the card, tagged with
    ``data-hl-arrayed-band`` so hooks + custom styling can target it.
    """
    assert "data-hl-arrayed-band" in bundle_js


def test_arrayed_frame_band_label(bundle_js: str) -> None:
    """Top band reads the bare word 「arrayed」 (no parens or brackets).

    JSX text children compile to a quoted string literal; check both quote
    flavours so the assertion survives esbuild's quote-mode picks.
    """
    assert '"arrayed"' in bundle_js or "'arrayed'" in bundle_js


def test_arrayed_header_suffix_removed(bundle_js: str) -> None:
    """The old 「(arrayed)」 header suffix must not linger — its role was
    fully replaced by the top band, and keeping both would double-count."""
    assert "(arrayed)" not in bundle_js


# ---------------------------------------------------------------------------
# Port visuals
# ---------------------------------------------------------------------------


def test_arrayed_port_data_attr(bundle_js: str) -> None:
    """Arrayed ports carry ``data-hl-arrayed-port`` for hooks + custom styling."""
    assert "data-hl-arrayed-port" in bundle_js
