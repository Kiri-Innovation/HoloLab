"""Guard the ``colmap-cams`` preview: iframe wiring + vendored ColmapUtil.

The ``colmap-cams`` output of ``colmap-sfm-cams-only`` renders via an
iframe pointed at the vendored ColmapUtil build under
``hololab/frontend/public/colmaputil/``. Three failure modes we want to
catch cheaply:

1. Someone drops the tag intercept in ``previews.tsx`` — the canvas
   silently falls back to ``BasicInfoPreview`` (a text-listing card
   instead of the 3D viewer).
2. Someone forgets to promote the tag in
   ``AlgorithmNode.FRONTEND_VIEWER_TAGS`` — the caret disappears
   whenever the gateway hasn't ingested the backend registry entry
   for the tag yet (typical during rolling upgrades).
3. Someone runs ``npm run build`` without refreshing the vendored
   ColmapUtil dist — the iframe 404s at load time.

We inspect the built dist for the string ``"colmap-cams"`` inside the
bundle (both tag routes must contain it, so we assert the count is
>=2), the iframe URL literal ``/colmaputil/index.html?embed=1``, and
the presence of ``public/colmaputil/index.html``. The manifest test
already exists in ``test_colmap_packs.py`` — this file targets the
frontend wiring only.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FRONTEND = _REPO_ROOT / "hololab" / "frontend"


def _find_dist_js() -> Path | None:
    dist_assets = _FRONTEND / "dist" / "assets"
    if not dist_assets.is_dir():
        return None
    matches = sorted(dist_assets.glob("index-*.js"))
    return matches[-1] if matches else None


def test_bundle_contains_colmap_cams_tag_routing() -> None:
    """Both routing sites (Preview intercept + FRONTEND_VIEWER_TAGS) must
    emit the literal ``colmap-cams`` string into the bundle. Two hits
    minimum — a lower count means one of the two was silently dropped
    and the caret / dispatch pair falls out of sync.
    """

    js_path = _find_dist_js()
    if js_path is None:
        pytest.skip("frontend dist not built — run `npm run build` first")
    js = js_path.read_text(encoding="utf-8", errors="replace")
    hits = js.count("colmap-cams")
    assert hits >= 2, (
        f"expected >=2 occurrences of 'colmap-cams' in the built bundle "
        f"(Preview intercept + FRONTEND_VIEWER_TAGS promotion), found {hits}"
    )


def test_bundle_contains_colmap_points_tag_routing() -> None:
    """Same guard for ``colmap-points`` — the tag needs to appear in
    the Preview dispatch intercept AND the FRONTEND_VIEWER_TAGS set.
    Losing either drops the caret from ``colmap-triangulate`` /
    ``colmap-assemble.points_by_frame`` outputs on any rolling upgrade.
    """

    js_path = _find_dist_js()
    if js_path is None:
        pytest.skip("frontend dist not built — run `npm run build` first")
    js = js_path.read_text(encoding="utf-8", errors="replace")
    hits = js.count("colmap-points")
    assert hits >= 2, (
        f"expected >=2 occurrences of 'colmap-points' in the built bundle "
        f"(Preview intercept + FRONTEND_VIEWER_TAGS promotion), found {hits}"
    )


def test_bundle_iframes_vendored_colmaputil() -> None:
    """ColmapCamsPreview must point the iframe at the vendored build.
    Sits inside the bundled JS as a literal string — url computed at
    call time, not import time, so minifiers keep it intact.
    """

    js_path = _find_dist_js()
    if js_path is None:
        pytest.skip("frontend dist not built — run `npm run build` first")
    js = js_path.read_text(encoding="utf-8", errors="replace")
    assert "/colmaputil/index.html?embed=1" in js, (
        "ColmapCamsPreview iframe src is missing from the bundle — "
        "the drawer will render a blank iframe"
    )


def test_vendored_colmaputil_dist_is_present() -> None:
    """The vendored ColmapUtil build must land under ``public/`` so
    Vite copies it into ``dist/colmaputil/`` at build time. Without
    this file the iframe 404s.
    """

    index = _FRONTEND / "public" / "colmaputil" / "index.html"
    assert index.is_file(), (
        f"missing {index.relative_to(_REPO_ROOT)} — rebuild ColmapUtil "
        f"and re-vendor per public/colmaputil/HOLOLAB_VENDORED.md"
    )
    html = index.read_text(encoding="utf-8", errors="replace")
    # Assets must be scoped under /colmaputil/ (built with base=/colmaputil/);
    # otherwise the browser looks for /assets/... at the HoloLab origin and
    # 404s on the ColmapUtil chunks.
    assert "/colmaputil/assets/" in html, (
        "vendored ColmapUtil dist wasn't built with base=/colmaputil/ — "
        "run `npm run build:embed` in ColmapUtil before re-vendoring"
    )
