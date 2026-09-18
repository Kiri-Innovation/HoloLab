"""SPA-aware ``StaticFiles`` for the gateway's ``/`` mount.

The frontend uses path-based routing (``/w/<workflow_id>``, ``/artifacts``,
etc.) instead of URL fragments. Fragments never reached the server, so
``StaticFiles`` could serve every URL by returning 404 for
non-file paths and letting the browser SPA read ``location.hash``. Path
routing changes that: a browser refresh on ``/w/abc123`` hits the
gateway with that literal path, ``StaticFiles`` finds no
``dist/w/abc123`` file, and returns 404 — the SPA never gets a chance
to render.

The fix is the standard SPA fallback: for any path that would 404 AND
looks like a browser navigation (not a bare asset request), serve
``index.html`` instead. The React app then reads
``window.location.pathname`` and picks the right screen.

The mount is installed *after* all ``/api/*``, ``/proxy/*`` and
``/ws/*`` routes in ``app.py`` — Starlette dispatches in registration
order, so those routes always win. The fallback here only runs for
paths that reached ``/`` unclaimed.
"""

from __future__ import annotations

from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles
from starlette.types import Scope


class SPAStaticFiles(StaticFiles):
    """``StaticFiles`` that serves ``index.html`` on 404 for SPA paths.

    Rules for the fallback:

    * A path with a file extension (``.js``, ``.css``, ``.png``, …) is
      a real asset request; if it's not on disk the 404 is genuine and
      we don't mask it — otherwise a typo in a script tag would
      silently return the HTML shell and the browser would fail with a
      confusing MIME error.
    * A path *without* an extension (``/w/abc``, ``/artifacts``, ``/``)
      is a client-side route; return ``index.html`` so the SPA can
      resolve it. This is what react-router, Vite preview, and every
      other SPA host do.
    """

    async def get_response(self, path: str, scope: Scope):  # type: ignore[override]
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            # Path-like ``foo/bar.svg`` splits to (…, ``.svg``) with an
            # extension; ``w/abc123`` splits to (…, "") — the latter is
            # our SPA fallback territory.
            last_seg = path.rsplit("/", 1)[-1]
            has_extension = "." in last_seg
            if has_extension:
                raise
            # Serve the SPA shell. ``super().get_response`` handles
            # ETag / Last-Modified etc. correctly for us.
            return await super().get_response("index.html", scope)
