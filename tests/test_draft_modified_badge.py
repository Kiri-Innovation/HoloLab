"""Guard the draft-modified badge near the Run button.

When the in-memory draft graph structurally differs from the latest snapshot,
WorkflowToolbar renders a ``data-hl-draft-modified`` span with the text
「已修改 · 运行将创建新快照」. Cosmetic-only changes (position, preview_open)
do NOT count as differences — only structural fields do
(nodes/algorithm_name/algorithm_version/params/assigned_node_id + edges).

Same bundle-inspection pattern as ``test_cobrowser_error_callout.py`` and
``test_preview_placeholder_bundle.py`` — the frontend has no vitest infra;
we grep the built dist for the copy + data-attributes the browser will see.
Skips gracefully when the dist isn't built.
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
# Data attribute — hooks for tests and custom tooling.
# ---------------------------------------------------------------------------


def test_draft_modified_data_attr_present(bundle_js: str) -> None:
    """``WorkflowToolbar`` renders a ``data-hl-draft-modified`` element."""
    assert "data-hl-draft-modified" in bundle_js


# ---------------------------------------------------------------------------
# Badge copy.
# ---------------------------------------------------------------------------


def test_modified_label_present(bundle_js: str) -> None:
    """Badge label: 「已修改」."""
    assert "已修改" in bundle_js


def test_fork_consequence_copy_present(bundle_js: str) -> None:
    """Badge consequence copy: 「运行将创建新快照」."""
    assert "运行将创建新快照" in bundle_js


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
