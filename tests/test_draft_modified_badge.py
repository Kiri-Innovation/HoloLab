"""Guard the Run History panel's structural-change sentinel row and current-run chip.

When the in-memory draft has structural changes that haven't been run yet,
RunsPanel renders a ``data-hl-draft-modified-row`` sentinel div with the text
「草稿有结构改动 · 待运行」above the real run rows. The sentinel is a non-button
div with a distinct amber tint — visually separate from real snapshot rows.

When a snapshot is currently open on the canvas, its row gets a
``data-hl-current-run`` chip labelled 「当前」.

Structural fields that count as changes: algorithm_name/version, params,
assigned_node_id, edges. Cosmetic fields (position, preview_open) are excluded.

Same bundle-inspection pattern as the other frontend tests — greps the built
dist for copy + data-attributes. Skips gracefully when dist isn't built.
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
# Structural-change sentinel row.
# ---------------------------------------------------------------------------


def test_draft_modified_row_data_attr_present(bundle_js: str) -> None:
    """``RunsPanel`` renders a ``data-hl-draft-modified-row`` sentinel div."""
    assert "data-hl-draft-modified-row" in bundle_js


def test_structural_change_label_present(bundle_js: str) -> None:
    """Sentinel label: 「草稿有结构改动」."""
    assert "草稿有结构改动" in bundle_js


def test_pending_run_copy_present(bundle_js: str) -> None:
    """Sentinel sub-label: 「待运行」."""
    assert "待运行" in bundle_js


# ---------------------------------------------------------------------------
# Current-run chip on the active run row.
# ---------------------------------------------------------------------------


def test_current_run_data_attr_present(bundle_js: str) -> None:
    """Active run row renders a ``data-hl-current-run`` chip."""
    assert "data-hl-current-run" in bundle_js


def test_current_run_chip_label_present(bundle_js: str) -> None:
    """Current-run chip label: 「当前」."""
    assert "当前" in bundle_js


# ---------------------------------------------------------------------------
# Structural fingerprint — object property names survive minification because
# they are accessed off external data objects (g.nodes, g.edges).
# ---------------------------------------------------------------------------


def test_structural_key_includes_algorithm_name(bundle_js: str) -> None:
    """``graphStructuralKey`` maps ``algorithm_name`` into the fingerprint."""
    assert "algorithm_name" in bundle_js


def test_structural_key_includes_assigned_node_id(bundle_js: str) -> None:
    """``graphStructuralKey`` maps ``assigned_node_id`` into the fingerprint."""
    assert "assigned_node_id" in bundle_js
