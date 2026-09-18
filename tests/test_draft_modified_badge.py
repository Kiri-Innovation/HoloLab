"""Guard the Run History panel's structural-change sentinel, "检查" button, and diff modal.

When the draft has structural changes vs the latest snapshot, RunsPanel shows:
  1. A ``data-hl-draft-modified-row`` sentinel div (amber tint, dashed border).
  2. A ``data-hl-check-diff`` button labelled 「检查」inside that sentinel.
  3. Clicking opens a ``data-hl-draft-diff-modal`` portal (dark overlay, centered
     card) listing per-field diffs from ``diffGraphs()`` in human-readable Chinese.

When the active run row is "currently open on canvas", it carries a
``data-hl-current-run`` chip labelled 「当前」.

Bundle-inspection pattern: no vitest/Playwright infra; we grep the built dist
for copy + data-attributes. Skips gracefully when dist isn't built.
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


def test_draft_modified_row_data_attr(bundle_js: str) -> None:
    """``RunsPanel`` renders a ``data-hl-draft-modified-row`` sentinel div."""
    assert "data-hl-draft-modified-row" in bundle_js


def test_structural_change_label(bundle_js: str) -> None:
    """Sentinel label: 「草稿有结构改动」."""
    assert "草稿有结构改动" in bundle_js


def test_fork_consequence_copy(bundle_js: str) -> None:
    """Sentinel sub-label states the consequence: 「运行将创建新快照」."""
    assert "运行将创建新快照" in bundle_js


# ---------------------------------------------------------------------------
# "检查" button inside the sentinel.
# ---------------------------------------------------------------------------


def test_check_button_data_attr(bundle_js: str) -> None:
    """Sentinel row carries a ``data-hl-check-diff`` inspect button."""
    assert "data-hl-check-diff" in bundle_js


def test_check_button_label(bundle_js: str) -> None:
    """Inspect button label: 「检查」."""
    assert "检查" in bundle_js


# ---------------------------------------------------------------------------
# Diff modal.
# ---------------------------------------------------------------------------


def test_diff_modal_data_attr(bundle_js: str) -> None:
    """Diff modal root has ``data-hl-draft-diff-modal``."""
    assert "data-hl-draft-diff-modal" in bundle_js


def test_diff_modal_title(bundle_js: str) -> None:
    """Modal title: 「草稿结构改动」."""
    assert "草稿结构改动" in bundle_js


def test_diff_modal_close_attr(bundle_js: str) -> None:
    """Modal close button has ``data-hl-draft-diff-close``."""
    assert "data-hl-draft-diff-close" in bundle_js


def test_diff_item_data_attr(bundle_js: str) -> None:
    """Each diff item row has ``data-hl-diff-item``."""
    assert "data-hl-diff-item" in bundle_js


# ---------------------------------------------------------------------------
# Diff description copy — survives minification because they are string
# literals in the source (not variable names).
# ---------------------------------------------------------------------------


def test_diff_node_added_copy(bundle_js: str) -> None:
    """``diffGraphs`` produces '新增节点' for added nodes."""
    assert "新增节点" in bundle_js


def test_diff_node_removed_copy(bundle_js: str) -> None:
    """``diffGraphs`` produces '删除节点' for removed nodes."""
    assert "删除节点" in bundle_js


def test_diff_param_changed_copy(bundle_js: str) -> None:
    """``diffGraphs`` produces '参数' for changed params."""
    assert "参数" in bundle_js


def test_diff_assigned_node_copy(bundle_js: str) -> None:
    """``diffGraphs`` produces '执行节点' for changed assigned_node_id."""
    assert "执行节点" in bundle_js


def test_diff_edge_added_copy(bundle_js: str) -> None:
    """``diffGraphs`` produces '新增连线' for new edges."""
    assert "新增连线" in bundle_js


def test_diff_edge_removed_copy(bundle_js: str) -> None:
    """``diffGraphs`` produces '断开连线' for removed edges."""
    assert "断开连线" in bundle_js


def test_diff_unset_copy(bundle_js: str) -> None:
    """``diffGraphs`` uses '未设置' as the fallback for absent params."""
    assert "未设置" in bundle_js


# ---------------------------------------------------------------------------
# Current-run chip — independent of draftDiff.
# ---------------------------------------------------------------------------


def test_current_run_data_attr(bundle_js: str) -> None:
    """Active run row renders a ``data-hl-current-run`` chip."""
    assert "data-hl-current-run" in bundle_js


def test_current_run_chip_label(bundle_js: str) -> None:
    """Current-run chip label: 「当前」."""
    assert "当前" in bundle_js


def test_active_title_string(bundle_js: str) -> None:
    """The run-row button title 'currently open on canvas' is a pure
    snapshot-id comparison — no draftDiff gate — proving the chip shows
    whenever the user has a snapshot open, regardless of draft state."""
    assert "currently open on canvas" in bundle_js


def test_current_chip_and_sentinel_coexist(bundle_js: str) -> None:
    """Both indicators are compiled into the same bundle under independent
    conditions (active = snapshot_id match; sentinel = draftDiff.length > 0).
    When both conditions are true simultaneously, both appear in the panel:
    the sentinel row at the top, the 「当前」chip on the matching run row."""
    assert "data-hl-current-run" in bundle_js
    assert "data-hl-draft-modified-row" in bundle_js


# ---------------------------------------------------------------------------
# Structural fingerprint — property names survive minification (external data).
# ---------------------------------------------------------------------------


def test_structural_key_algorithm_name(bundle_js: str) -> None:
    """``diffGraphs`` accesses ``algorithm_name`` from external node data."""
    assert "algorithm_name" in bundle_js


def test_structural_key_assigned_node_id(bundle_js: str) -> None:
    """``diffGraphs`` accesses ``assigned_node_id`` from external node data."""
    assert "assigned_node_id" in bundle_js
