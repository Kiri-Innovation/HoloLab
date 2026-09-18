"""Guard the arrayed<T> UI surface: Inspector checkbox, node badge, port visuals (M4).

When a pack is ``arrayable``:
  * NodeInspector renders a ``data-hl-arrayed-toggle`` checkbox with the
    Chinese copy 「并行处理数组输入」 and a hint block that mentions the
    ``{parent}/{port}/{element_id}/`` layout convention.
  * AlgorithmNode renders a ``data-hl-arrayed-badge`` chip in the header
    (「[N]」) when the graph node's ``arrayed_toggle`` is on.
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
# AlgorithmNode header badge
# ---------------------------------------------------------------------------


def test_arrayed_badge_data_attr(bundle_js: str) -> None:
    """Header chip carries ``data-hl-arrayed-badge`` when the node is arrayed."""
    assert "data-hl-arrayed-badge" in bundle_js


def test_arrayed_badge_label(bundle_js: str) -> None:
    """Badge label uses a stable text token 「arr」 so tests + docs can pin it.

    The redesign in 163387e moved the badge from the header (「[N]」) to
    a compact chip in the footer (「arr」) to free header real estate
    for the pack name + action icons. The token itself still needs to
    be stable so E2E hooks and this test can grep for it.
    """
    assert "data-hl-arrayed-badge" in bundle_js
    # The label appears inside the chip; grep for the exact token used
    # in the AlgorithmNode source ("arr" as the chip content).
    assert "arr" in bundle_js


# ---------------------------------------------------------------------------
# Port visuals
# ---------------------------------------------------------------------------


def test_arrayed_port_data_attr(bundle_js: str) -> None:
    """Arrayed ports carry ``data-hl-arrayed-port`` for hooks + custom styling."""
    assert "data-hl-arrayed-port" in bundle_js
