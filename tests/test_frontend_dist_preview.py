"""Guard the built preview-viewer dispatch table.

The frontend's ``previews.tsx`` dispatches on ``spec.viewer`` — one
``case`` per known viewer. If a case is dropped (or the source is
rebuilt without a rebuild picking up the new viewer), the browser falls
through to the ``unknown viewer: <name>`` error state and users see
nothing. That's the failure mode we hit when the ``video-grid`` viewer
first shipped: the source had the case, the dist was fresh, but a stale
browser cache still served an older bundle. This test cheaply guarantees
that the *bundle currently on disk* actually contains every declared
case string, so a broken build never lands unnoticed.

We inspect the built dist (rather than running vitest against the source)
because the frontend has no other test infra today; wiring up vitest for
one assertion would be a bigger step than the guarantee is worth.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# One entry per viewer the frontend's OutputPreviewSpec knows about.
# The source of truth is ``hololab/frontend/src/wire.ts`` — keep this in
# sync when adding a viewer.
_EXPECTED_VIEWERS = ("splatv", "video", "image", "text", "video-grid")


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


def test_built_dist_dispatches_every_declared_viewer() -> None:
    """Assert the built bundle contains a ``case "<viewer>"`` line for
    every viewer declared in the OutputPreviewSpec union.

    Skips gracefully when the dist isn't built yet (CI without frontend
    build stage — that path is covered by the manual ``npm run build``
    step in the deploy loop). If the dist IS built, we're strict.
    """

    js_path = _find_dist_js()
    if js_path is None:
        pytest.skip("frontend dist not built — run `npm run build` first")
    js = js_path.read_text(encoding="utf-8", errors="replace")

    # Minifiers may quote with ' or ", and may or may not put a space
    # after ``case``. Check both quote flavours; any minifier variation
    # beyond that would break the switch itself, which is not a "silently
    # dispatched to default" failure mode.
    for viewer in _EXPECTED_VIEWERS:
        assert (
            f'case"{viewer}"' in js
            or f"case'{viewer}'" in js
            or f'case "{viewer}"' in js
            or f"case '{viewer}'" in js
        ), (
            f"built dist {js_path.name} is missing dispatch for viewer "
            f"{viewer!r}; the browser would fall through to the "
            f"'unknown viewer' fallback"
        )
