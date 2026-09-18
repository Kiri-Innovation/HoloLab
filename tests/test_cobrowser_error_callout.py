"""Guard Cobrowser button error callouts in the built frontend bundle.

``OpenSourceButton`` and ``OpenInCocoderButton`` used to show only a red
exclamation icon on every failure — the reason code was in the tooltip
(``title`` attribute) but invisible without hovering. Both components now
render a ``SourceErrorCallout`` (``data-hl-source-error``) with a
human-readable title + optional detail for each failure path:

  * ``not-found``       — file not found on the device
  * ``outside-roots``   — path outside Cocoder workspace roots
  * ``declined``        — user cancelled the confirmation card
  * ``busy``            — another showDocument is waiting for confirmation
  * ``timeout``         — confirmation card timed out
  * ``forbidden``       — page origin blocked by Cobrowser API
  * ``invalid-path``    — path rejected as syntactically invalid
  * ``unavailable`` / ``bad-response`` / ``error`` — Cobrowser internal

No vitest infra — we inspect the built bundle exactly like
``test_frontend_dist_preview.py`` does for the viewer dispatch table.
Skip gracefully when the dist is not built.
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
# SourceErrorCallout component present
# ---------------------------------------------------------------------------


def test_source_error_callout_data_attr_present(bundle_js: str) -> None:
    """The SourceErrorCallout renders a ``data-hl-source-error`` element."""
    assert "data-hl-source-error" in bundle_js


# ---------------------------------------------------------------------------
# Failure messages for OpenSourceButton (code-icon, canvas node header)
# ---------------------------------------------------------------------------


def test_open_source_not_found_msg(bundle_js: str) -> None:
    assert "File not found on" in bundle_js


def test_open_source_outside_roots_msg(bundle_js: str) -> None:
    assert "Not inside a Cocoder workspace root" in bundle_js


def test_open_source_declined_msg(bundle_js: str) -> None:
    assert "Cancelled" in bundle_js and "click again to retry" in bundle_js


def test_open_source_busy_msg(bundle_js: str) -> None:
    assert "Another confirmation is pending" in bundle_js


def test_open_source_timeout_msg(bundle_js: str) -> None:
    assert "Confirmation timed out" in bundle_js


def test_open_source_forbidden_msg(bundle_js: str) -> None:
    assert "Cocoder blocked this origin" in bundle_js


def test_open_source_internal_error_msg(bundle_js: str) -> None:
    assert "Cocoder internal error" in bundle_js


def test_open_source_path_unknown_msg(bundle_js: str) -> None:
    """``!target`` branch — manifest_path unknown on this node."""
    assert "Source path unknown" in bundle_js


# ---------------------------------------------------------------------------
# OpenInCocoderButton (↗ preview-drawer button) same callouts
# ---------------------------------------------------------------------------


def test_open_cocoder_not_found_detail(bundle_js: str) -> None:
    assert "File not found on this device" in bundle_js


def test_open_cocoder_outside_roots_detail(bundle_js: str) -> None:
    # Both buttons share the same message — one assertion covers both.
    assert "Add this path" in bundle_js and "workspace roots" in bundle_js
