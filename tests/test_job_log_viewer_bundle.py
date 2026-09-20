"""Bundle guard for the Recent Jobs → job-log viewer surface.

When the RecentJobsPanel renders a row it emits:
  * ``data-hl-view-log`` — the compact "view log" icon button, sibling of
    the existing copy-ref button. The Playwright / E2E harness selects
    on this instead of grepping button geometry.

Opening it mounts ``JobLogModal`` which emits:
  * ``data-hl-job-log-modal``   — the overlay container.
  * ``data-hl-job-log-body``    — the dark, monospace, scrollable log area.
  * ``data-hl-job-log-line``    — one row per log line, with data-hl-stream
    and (for highlighted errors) data-hl-error="1".
  * ``data-hl-job-log-fail``    — the red banner rendered above the log
    area when the job carries a fail_reason / fail_message.
  * ``data-hl-job-log-copy``    — the copy-whole-log button in the header.
  * ``data-hl-job-log-truncated`` — the "log longer than N lines" hint.

Same bundle-inspection pattern as test_arrayed_ui_bundle: grep the built
dist for the data-attributes and stable UI copy. Skips gracefully when
the dist hasn't been built.
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


# ---------------------------------------------------------------------------
# Row-level entry point on the Recent Jobs panel
# ---------------------------------------------------------------------------


def test_row_view_log_button_data_attr(bundle_js: str) -> None:
    """Each RecentJobsPanel row renders a ``data-hl-view-log`` button."""

    assert "data-hl-view-log" in bundle_js


# ---------------------------------------------------------------------------
# Modal shell + body + header + footer
# ---------------------------------------------------------------------------


def test_modal_shell_data_attr(bundle_js: str) -> None:
    """JobLogModal renders with ``data-hl-job-log-modal`` on its overlay."""

    assert "data-hl-job-log-modal" in bundle_js


def test_modal_body_data_attr(bundle_js: str) -> None:
    """The scrollable log area carries ``data-hl-job-log-body``."""

    assert "data-hl-job-log-body" in bundle_js


def test_modal_line_data_attrs(bundle_js: str) -> None:
    """Each line row carries ``data-hl-job-log-line`` + ``data-hl-stream``.

    Error highlighting flips ``data-hl-error`` — E2E hooks use it to
    confirm the "stderr Traceback" case still lights up.
    """

    assert "data-hl-job-log-line" in bundle_js
    assert "data-hl-stream" in bundle_js
    assert "data-hl-error" in bundle_js


def test_modal_fail_banner_data_attr(bundle_js: str) -> None:
    """When the job has a fail_reason/message the header shows a red
    banner tagged ``data-hl-job-log-fail`` so the viewer can render both
    (a) the operator-visible summary and (b) the raw log below it."""

    assert "data-hl-job-log-fail" in bundle_js


def test_modal_copy_button_data_attr(bundle_js: str) -> None:
    """Header exposes ``data-hl-job-log-copy`` — the copy-whole-log button.

    Distinct from the row's copy-ref button (which copies a
    ``hololab://job/…`` reference, not the log body).
    """

    assert "data-hl-job-log-copy" in bundle_js


def test_modal_truncated_hint_data_attr(bundle_js: str) -> None:
    """The truncated-tail footer hint carries ``data-hl-job-log-truncated``."""

    assert "data-hl-job-log-truncated" in bundle_js


def test_modal_empty_state_carries_data_attr(bundle_js: str) -> None:
    """The empty-body placeholder carries ``data-hl-job-log-empty`` so an
    E2E probe can distinguish "no logs, framework failure" (renders
    fail_message inline) from "no logs, still loading" (spinner text).

    Guard against the specific regression this attribute was added
    for: a shard whose ``handle_locate`` failed at the framework layer
    has zero log lines but a populated ``fail_message`` — the modal
    used to render only "No log lines yet…" and hide the actual
    reason behind the compact banner. The empty-state branch now
    always renders the fail_message when present.
    """

    assert "data-hl-job-log-empty" in bundle_js
