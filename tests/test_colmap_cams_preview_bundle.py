"""Type-declared COLMAP viewers still reach the shipped renderer assets."""

import os
from pathlib import Path

import pytest

from hololab.gateway.tag_viewers import infer_preview_for_output

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FRONTEND = _REPO_ROOT / "hololab/frontend"
_DIST = Path(os.environ.get("HOLOLAB_TEST_FRONTEND_DIST", _FRONTEND / "dist"))


def _find_dist_js():
    matches = sorted((_DIST / "assets").glob("index-*.js"))
    return matches[-1] if matches else None


@pytest.mark.parametrize(
    "tag,viewer",
    [
        ("colmap-cams", "colmap-cams"),
        ("point-cloud", "colmap-points"),
        ("colmap-points", "colmap-points"),
        ("colmap-folder", "colmap-frame"),
        ("colmap-frame", "colmap-frame"),
        ("colmap", "colmap-frame"),
    ],
)
def test_declared_viewer_is_shipped(tag, viewer):
    assert infer_preview_for_output(None, [tag], "dir").viewer == viewer
    path = _find_dist_js()
    if path is None:
        pytest.skip("frontend dist not built")
    assert viewer in path.read_text()


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
    """The generated ColmapUtil build must reach ``dist/colmaputil/``.
    Source-only Python checkouts skip this assertion; frontend CI builds
    first, so a missing iframe or accidentally packaged VSIX fails there.
    """

    if _find_dist_js() is None:
        pytest.skip("frontend dist not built — run `npm run build` first")
    index = _DIST / "colmaputil" / "index.html"
    assert index.is_file(), (
        f"missing {index} — rebuild ColmapUtil via npm run build; see vendor/HOLOLAB_VENDORED.md"
    )
    assert not list(index.parent.rglob("*.vsix")), "VSIX must not ship in the frontend"
    html = index.read_text(encoding="utf-8", errors="replace")
    # Assets must be scoped under /colmaputil/ (built with base=/colmaputil/);
    # otherwise the browser looks for /assets/... at the HoloLab origin and
    # 404s on the ColmapUtil chunks.
    assert "/colmaputil/assets/" in html, (
        "vendored ColmapUtil dist wasn't built with base=/colmaputil/ — "
        "run `npm run build:vendor` in hololab/frontend"
    )
