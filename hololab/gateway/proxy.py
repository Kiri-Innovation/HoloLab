"""Preview reverse proxy — ``GET /proxy/{node_id}/{path}``.

The browser cannot reach a NAT-hidden node directly; the gateway forwards
read-only file requests to the node's local HTTP file server. See
docs/architecture.md#preview-channel.
"""

from __future__ import annotations

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from hololab.gateway.registry import NodeRegistry, NodeSession


def strip_workspace_prefix(session: NodeSession | None, absolute_path: str) -> str:
    """Convert a handle's absolute path into the proxy sub-path.

    Tries the node's primary workspace_root first, then any
    legacy_workspace_roots in list order. Returns the first successful
    strip; falls back to the raw absolute path when nothing matches so
    the URL is at least visible for diagnostics (the file server will
    404 in that case, but preview UIs surface the error cleanly).

    This helper is the one place path stripping should live — it keeps
    the three ``session.workspace_root`` call sites in agreement about
    the legacy-root fallback semantics.
    """

    if session is None:
        return absolute_path
    candidates: list[str] = []
    if session.workspace_root:
        candidates.append(session.workspace_root)
    candidates.extend(session.legacy_workspace_roots or [])
    for root in candidates:
        stripped = root.rstrip("/")
        if absolute_path.startswith(stripped + "/"):
            return absolute_path[len(stripped) + 1 :]
    return absolute_path


# Response headers we forward from the node back to the browser. The
# missing pieces in the pre-streaming build caused user-visible
# stalls in the video-grid drawer:
#
#   * ``accept-ranges``: without this, browsers don't discover they can
#     Range-request. They fall back to a single full GET, which for a
#     55 MiB zoom-mode source means "stream nothing until every byte is
#     buffered somewhere".
#   * ``content-range``: required for 206 Partial Content responses to
#     be interpretable at all.
#
# ``cache-control`` gets passed through so a cache-friendly node
# response (e.g. a long max-age on transcoded previews) survives the
# hop. Other node-side headers are dropped so a misconfigured file
# server can't leak sensitive metadata into a public preview URL.
_PASSTHROUGH_RESPONSE_HEADERS = frozenset(
    {
        "content-type",
        "content-length",
        "content-range",
        "accept-ranges",
        "etag",
        "last-modified",
        "cache-control",
    }
)


# One shared client for the whole process. Browsers stream MP4 by
# opening open-ended Range requests, reading enough to feed their
# forward buffer, then aborting and re-opening at a fresh offset. On
# a 10 s cook_spinach zoom playthrough we observed 8 such range
# aborts. Under the previous "``httpx.AsyncClient`` per request"
# pattern every abort threw away a warm TCP connection to the node
# and paid a fresh handshake on the next range — measurable as
# 5-10 ms of extra latency per range on localhost, and the "video
# plays one segment at a time" symptom the user reported when
# testing against a live gateway.
#
# httpx's connection pool keeps up to ``max_keepalive_connections``
# sockets open per host; those slots are what the reopened Range
# requests grab from. ``max_connections`` is a hard ceiling that
# absorbs a 21-tile drawer opening cold (each tile keep-alive slot
# is one connection).
#
# The client is created lazily on first use so import-time doesn't
# require a running event loop, and stashed in a module global so
# the same instance survives across proxy calls. Process exit tears
# down the sockets — we don't need explicit cleanup.
_shared_client: httpx.AsyncClient | None = None


def _get_shared_client() -> httpx.AsyncClient:
    global _shared_client
    if _shared_client is None:
        _shared_client = httpx.AsyncClient(
            # Bound connect/write, leave ``read`` unbounded — a slow
            # browser can legitimately hold the stream open for the
            # duration of a long video. Client-side disconnect
            # closes the socket and the ``finally`` block in
            # ``_iter`` returns the connection to the pool.
            timeout=httpx.Timeout(connect=5.0, read=None, write=30.0, pool=5.0),
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
        )
    return _shared_client


async def _reset_shared_client() -> None:
    """Tear the shared client down. For tests to call between cases
    where ``httpx.AsyncClient`` has been monkey-patched — otherwise a
    stale (pre-patch) client survives and swallows the mock.
    """

    global _shared_client
    if _shared_client is not None:
        await _shared_client.aclose()
        _shared_client = None


async def proxy_get(node_id: str, path: str, request: Request, registry: NodeRegistry) -> Response:
    """Fetch ``path`` from the given node's file server and stream it back.

    Uses ``httpx.AsyncClient.send(..., stream=True)`` so upstream bytes
    reach the browser as soon as they arrive on the socket rather than
    after the whole body is buffered in gateway memory. This matters
    twice:

      * TTFB stays a few ms even for large files. A prior
        ``client.get()`` path buffered the *entire* upstream response
        first, which added ~290 ms of latency to a 55 MiB zoom-mode
        stream and ~10 ms per 94 KiB preview tile.
      * Range-request semantics survive intact: the browser can seek
        into a video without waiting for the gateway to have
        pre-buffered the requested slice.

    The ``httpx.AsyncClient`` is shared process-wide (see
    ``_get_shared_client``). The response object still gets an
    explicit ``aclose()`` in the streaming ``finally`` so the
    underlying connection returns to the pool for the next Range
    request instead of being torn down.
    """

    session = registry.get_session(node_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"node {node_id!r} is not connected")

    if not session.advertised_url:
        raise HTTPException(
            status_code=502,
            detail=f"node {node_id!r} did not advertise a file-server URL",
        )

    # Compose target URL; the node's local server treats ``path`` as a file
    # under its published preview root. Query string is forwarded so that
    # endpoints like ``/_thumb/{WxH}/{sub}?at=N`` reach the node with their
    # parameters intact.
    target = session.advertised_url.rstrip("/") + "/" + path.lstrip("/")
    if request.url.query:
        target = f"{target}?{request.url.query}"

    # Forward Range for large streaming, drop hop-by-hop headers.
    forward_headers = {}
    for name, value in request.headers.items():
        if name.lower() in ("range", "if-modified-since", "if-none-match"):
            forward_headers[name] = value

    client = _get_shared_client()
    req = client.build_request("GET", target, headers=forward_headers)
    try:
        upstream = await client.send(req, stream=True)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"upstream fetch failed: {exc}") from exc

    async def _iter() -> object:
        try:
            # ``aiter_raw`` streams undecoded bytes; content-length /
            # content-encoding pass through as the upstream set them.
            # We're a byte-transparent proxy, not a content decoder.
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            # Release the connection back to the pool. The shared
            # client itself stays alive for the next request.
            await upstream.aclose()

    response_headers = {
        k: v
        for k, v in upstream.headers.items()
        if k.lower() in _PASSTHROUGH_RESPONSE_HEADERS
    }

    return StreamingResponse(
        _iter(),
        status_code=upstream.status_code,
        headers=response_headers,
    )
