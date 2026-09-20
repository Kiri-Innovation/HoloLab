"""Guard the mobile portrait shell (canvas/MobileShell.tsx).

The shell is only visible below the 900px breakpoint; above it, the desktop
grid renders untouched. This test checks the built bundle for the markers
that prove:

  * The five-tab switcher (Packs / Nodes / Runs / Jobs / Inspect) shipped
    with the correct labels and stable ``data-hl-mobile-tab`` hooks.
  * The active-tab attribute (``data-hl-mobile-active-tab``) is emitted for
    E2E hooks + debugging.
  * The breakpoint media query is the 900px one, so the desktop layout
    stays untouched at ≥901px.

Bundle-inspection pattern, matching test_arrayed_ui_bundle.py — no live
browser, no vitest infrastructure, skip cleanly if the dist isn't built.
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
# Shell markers
# ---------------------------------------------------------------------------


def test_mobile_shell_active_tab_attr(bundle_js: str) -> None:
    """Root element carries ``data-hl-mobile-active-tab`` so tests can
    read the current tab without introspecting React state."""
    assert "data-hl-mobile-active-tab" in bundle_js


def test_mobile_shell_tab_hooks(bundle_js: str) -> None:
    """Every tab button carries ``data-hl-mobile-tab`` so E2E can click
    a specific tab by key rather than by label position."""
    assert "data-hl-mobile-tab" in bundle_js


# ---------------------------------------------------------------------------
# Breakpoint
# ---------------------------------------------------------------------------


def test_mobile_breakpoint_is_900px(bundle_js: str) -> None:
    """``matchMedia("(max-width: 900px)")`` — the desktop layout must
    remain the sole path at ≥901px, so this exact query must survive
    minification."""
    assert "max-width: 900px" in bundle_js or "max-width:900px" in bundle_js


# ---------------------------------------------------------------------------
# Tab labels
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", ["Packs", "Nodes", "Runs", "Jobs", "Inspect"])
def test_tab_label_present(bundle_js: str, label: str) -> None:
    """All five short tab labels are emitted as string literals."""
    assert f'"{label}"' in bundle_js or f"'{label}'" in bundle_js
