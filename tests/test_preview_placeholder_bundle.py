"""Guard the preview-drawer "artifact cleaned" / "never ran" placeholders.

When a graph node's output has been tombstoned via
``DELETE /api/artifacts/{id}`` (or the node has never run), the canvas
preview drawer should render ``PreviewPlaceholder`` instead of a broken
<video>/<img> — the placeholder shows a specific caption and a "Run
this node" button that hits the existing
``POST /api/workflows/{wid}/dispatch/{gnid}`` endpoint.

Same bundle-inspection pattern as ``test_frontend_dist_preview.py`` and
``test_cobrowser_error_callout.py`` — the frontend has no vitest infra;
we check the built dist for the copy + data-attributes the browser will
see. Skips gracefully when the dist isn't built.
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
# Placeholder component structure — data attrs are how tests hook in.
# ---------------------------------------------------------------------------


def test_placeholder_data_attr_present(bundle_js: str) -> None:
    """``PreviewPlaceholder`` renders a ``data-hl-preview-placeholder`` element."""
    assert "data-hl-preview-placeholder" in bundle_js


def test_run_node_data_attr_present(bundle_js: str) -> None:
    """The Run button carries a ``data-hl-run-node`` attribute for hooks."""
    assert "data-hl-run-node" in bundle_js


# ---------------------------------------------------------------------------
# Placeholder copy — Chinese text the two states show.
# ---------------------------------------------------------------------------


def test_cleaned_title_present(bundle_js: str) -> None:
    """Cleaned-state title: `产物已被清理`."""
    assert "产物已被清理" in bundle_js


def test_never_ran_title_present(bundle_js: str) -> None:
    """Never-ran-state title: `尚未运行`."""
    assert "尚未运行" in bundle_js


def test_run_button_label_present(bundle_js: str) -> None:
    """Run button label — `运行本节点`."""
    assert "运行本节点" in bundle_js


def test_running_state_label_present(bundle_js: str) -> None:
    """Live-run state label: `运行中…`."""
    assert "运行中" in bundle_js


def test_dispatch_error_prefix_present(bundle_js: str) -> None:
    """Dispatch-failure message prefix: `分派失败`."""
    assert "分派失败" in bundle_js


# ---------------------------------------------------------------------------
# Wiring — event name the placeholder bridges through to the App.
# ---------------------------------------------------------------------------


def test_run_node_event_name_present(bundle_js: str) -> None:
    """AlgorithmNode dispatches ``hololab-run-node`` for the App to bridge."""
    assert "hololab-run-node" in bundle_js


def test_dispatch_endpoint_shape_present(bundle_js: str) -> None:
    """App bridges the event onto ``/api/workflows/.../dispatch/...``."""
    assert "/dispatch/" in bundle_js
    assert "/api/workflows/" in bundle_js
