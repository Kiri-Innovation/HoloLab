"""Guard the SPA fallback behind the gateway's ``/`` static mount.

Path-based routing (``/w/<workflow_id>``, ``/artifacts``) means the
browser hits the gateway with paths that don't exist on disk — no
``dist/w/`` directory. ``StaticFiles`` alone would 404 those, breaking
refresh / deep-link. ``SPAStaticFiles`` (installed in
``hololab/gateway/spa_staticfiles.py``) rewrites the 404 to serve
``index.html`` for extension-less paths so React can pick up the route
client-side.

Rules we lock in:

* Extension-less unknown paths → 200 + ``index.html`` bytes.
* Paths with a real asset extension (``.js``/``.css``/``.png``) that
  DON'T exist → real 404 (otherwise a typo in a script src would
  silently return HTML and blow up with a MIME error in the browser).
* ``/api/*`` and ``/proxy/*`` still take precedence and get real 404s
  when the ids are unknown (the fallback only fires for un-matched
  requests that reach the static mount).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hololab.gateway.app import create_app


def _dist_index_bytes() -> bytes | None:
    dist = (
        Path(__file__).resolve().parent.parent
        / "hololab"
        / "frontend"
        / "dist"
        / "index.html"
    )
    if not dist.is_file():
        return None
    return dist.read_bytes()


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    if _dist_index_bytes() is None:
        pytest.skip("frontend dist not built — run `npx vite build` first")
    db = tmp_path_factory.mktemp("spa") / "gateway.sqlite"
    app = create_app(db_path=db)
    with TestClient(app) as tc:
        yield tc


def test_spa_fallback_workflow_path(client: TestClient) -> None:
    """``/w/<id>`` returns the SPA shell so the SPA can pick up the route."""
    r = client.get("/w/whatever-id-not-on-disk")
    assert r.status_code == 200
    body = r.content
    assert b"<div id=\"root\"></div>" in body or b'id="root"' in body


def test_spa_fallback_artifacts_path(client: TestClient) -> None:
    """``/artifacts`` returns the SPA shell (not a real dist/artifacts file)."""
    r = client.get("/artifacts")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")


def test_asset_404_is_not_swallowed(client: TestClient) -> None:
    """A missing ``.js`` / ``.css`` must 404, not silently return HTML.

    Otherwise a typo in a script tag would fetch the SPA shell as
    ``application/javascript`` and the browser would throw a MIME
    mismatch error deep inside the console.
    """
    r = client.get("/assets/does-not-exist.js")
    assert r.status_code == 404


def test_api_unknown_still_404(client: TestClient) -> None:
    """The fallback must not shadow ``/api/*`` — a missing endpoint
    should return the real 404, not the SPA shell."""
    r = client.get("/api/handles/no-such-handle")
    # 404 (from the handles route's own guard) or 4xx JSON — anything
    # but 200 with HTML.
    assert r.status_code != 200
    assert not r.headers.get("content-type", "").startswith("text/html")


def test_index_html_still_at_root(client: TestClient) -> None:
    """Root ``/`` still serves ``index.html`` via StaticFiles' html=True.
    This isn't a fallback path — it's the normal static serving — but
    covering it keeps a regression from silently breaking the landing.
    """
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
