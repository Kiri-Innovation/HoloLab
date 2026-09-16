"""Proxy header pass-through + streaming behaviour.

The video-grid drawer relies on the browser being able to Range-request
into an on-demand-transcoded ``/_preview/…`` MP4. That falls apart if
the gateway proxy either (a) strips ``Accept-Ranges`` from the upstream
response — so the browser thinks Range isn't supported and won't try
— or (b) fully buffers the upstream body before forwarding, which
adds real latency on every seek. Both were live bugs prior to the
``feat(gateway): stream proxy_get`` change; these tests lock in the
fix.

The tests use ``httpx.MockTransport`` to inject synthetic upstream
responses so we can assert per-header pass-through without needing a
real node process.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from fastapi import Request

from hololab.gateway import proxy as proxy_mod

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass
class _FakeSession:
    advertised_url: str | None = "http://node.local:9999"
    workspace_root: str | None = None
    legacy_workspace_roots: list[str] = field(default_factory=list)


@dataclass
class _FakeRegistry:
    session: _FakeSession | None

    def get_session(self, node_id: str) -> _FakeSession | None:
        return self.session


def _make_request(
    headers: dict[str, str] | None = None, query: str = ""
) -> Request:
    """Build a minimal Starlette Request for proxy_get to introspect."""
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": "/proxy/node/x.mp4",
        "raw_path": b"/proxy/node/x.mp4",
        "query_string": query.encode(),
        "headers": [
            (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()
        ],
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 0),
    }
    return Request(scope)


def _install_mock_transport(
    monkeypatch: pytest.MonkeyPatch, handler: Any
) -> None:
    """Swap the module-level ``httpx.AsyncClient`` so it uses a
    ``MockTransport`` instead of talking to a real socket, and reset
    the shared-client singleton so it re-creates against the mock.
    """

    transport = httpx.MockTransport(handler)
    original = proxy_mod.httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(proxy_mod.httpx, "AsyncClient", factory)
    # Force the next ``_get_shared_client`` call to construct a fresh
    # client using the just-installed factory. Otherwise a client
    # cached from a previous test uses the real transport.
    proxy_mod._shared_client = None


async def _collect_body(response: Any) -> bytes:
    """StreamingResponse body is an async iterator; drain it."""
    chunks: list[bytes] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode())
    return b"".join(chunks)


def _streaming_body(*parts: bytes):
    """Async generator that yields ``parts`` in order.

    ``httpx.MockTransport`` pre-consumes bodies passed as ``bytes``,
    which makes them un-``aiter_raw()``-able. Feeding an async
    generator keeps the response in a "stream still available" state
    that matches how a real socket-backed upstream behaves.
    """

    async def gen():
        for p in parts:
            yield p

    return gen()


# ---------------------------------------------------------------------------
# Header pass-through
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proxy_passes_accept_ranges_from_upstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: ``Accept-Ranges: bytes`` from the node must survive
    the hop. Without this the browser skips Range requests and can't
    stream / seek video previews without fully downloading them.
    """

    upstream_headers = {
        "content-type": "video/mp4",
        "content-length": "4",
        "accept-ranges": "bytes",
        "cache-control": "max-age=3600",
        "etag": '"deadbeef"',
        "last-modified": "Wed, 21 Oct 2015 07:28:00 GMT",
        # These MUST NOT propagate — a misconfigured node shouldn't
        # leak arbitrary headers into a public preview URL.
        "server": "sekrit-uvicorn/1.0",
        "x-node-token": "sensitive",
        "set-cookie": "SESSION=abc",
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers=upstream_headers, content=_streaming_body(b"abcd")
        )

    _install_mock_transport(monkeypatch, handler)

    resp = await proxy_mod.proxy_get(
        "node",
        "x.mp4",
        _make_request(),
        _FakeRegistry(_FakeSession()),  # type: ignore[arg-type]
    )

    assert resp.status_code == 200
    hdrs = {k.lower(): v for k, v in resp.headers.items()}
    assert hdrs.get("accept-ranges") == "bytes"
    assert hdrs.get("cache-control") == "max-age=3600"
    assert hdrs.get("etag") == '"deadbeef"'
    assert hdrs.get("content-type") == "video/mp4"
    assert hdrs.get("content-length") == "4"
    # Passthrough allow-list — anything else is dropped.
    assert "x-node-token" not in hdrs
    assert "set-cookie" not in hdrs

    assert await _collect_body(resp) == b"abcd"


@pytest.mark.asyncio
async def test_proxy_passes_content_range_on_206(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the browser Range-requests a specific slice, the node
    replies 206 with ``Content-Range``. Both must reach the browser
    or the range response is uninterpretable.
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("range") == "bytes=0-1023"
        return httpx.Response(
            206,
            headers={
                "content-type": "video/mp4",
                "content-length": "1024",
                "content-range": "bytes 0-1023/94425",
                "accept-ranges": "bytes",
            },
            content=_streaming_body(b"\x00" * 1024),
        )

    _install_mock_transport(monkeypatch, handler)

    resp = await proxy_mod.proxy_get(
        "node",
        "x.mp4",
        _make_request(headers={"range": "bytes=0-1023"}),
        _FakeRegistry(_FakeSession()),  # type: ignore[arg-type]
    )

    assert resp.status_code == 206
    hdrs = {k.lower(): v for k, v in resp.headers.items()}
    assert hdrs.get("content-range") == "bytes 0-1023/94425"
    assert hdrs.get("accept-ranges") == "bytes"
    assert len(await _collect_body(resp)) == 1024


@pytest.mark.asyncio
async def test_proxy_forwards_conditional_request_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``If-None-Match`` + ``If-Modified-Since`` must reach the node
    so it can return 304 when appropriate — otherwise the browser
    can't use its cache.
    """

    seen_headers: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        for k in ("range", "if-none-match", "if-modified-since"):
            if k in request.headers:
                seen_headers[k] = request.headers[k]
        return httpx.Response(304, content=_streaming_body(b""))

    _install_mock_transport(monkeypatch, handler)

    resp = await proxy_mod.proxy_get(
        "node",
        "x.mp4",
        _make_request(
            headers={
                "if-none-match": '"deadbeef"',
                "if-modified-since": "Wed, 21 Oct 2015 07:28:00 GMT",
                # Not in the forward list — must NOT propagate.
                "authorization": "Bearer sekrit",
            }
        ),
        _FakeRegistry(_FakeSession()),  # type: ignore[arg-type]
    )

    assert resp.status_code == 304
    assert seen_headers.get("if-none-match") == '"deadbeef"'
    assert seen_headers.get("if-modified-since") == "Wed, 21 Oct 2015 07:28:00 GMT"
    assert "authorization" not in seen_headers


# ---------------------------------------------------------------------------
# Streaming behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proxy_streams_upstream_chunks_incrementally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The proxy must forward upstream bytes as they arrive, not
    buffer the whole body first. This is what keeps TTFB low on a 55
    MiB zoom-mode source (pre-fix, ``client.get()`` added ~290 ms).

    We verify by having the upstream produce a body that arrives in
    known chunks and confirming the proxy yields each chunk without
    coalescing.
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "video/mp4"},
            content=_streaming_body(b"chunk-1;", b"chunk-2;", b"chunk-3;"),
        )

    _install_mock_transport(monkeypatch, handler)

    resp = await proxy_mod.proxy_get(
        "node",
        "x.mp4",
        _make_request(),
        _FakeRegistry(_FakeSession()),  # type: ignore[arg-type]
    )

    # StreamingResponse exposes an async iterator — drain and verify
    # we saw at least two chunks (i.e. bytes weren't coalesced into
    # one big blob by an intermediate buffer).
    chunks: list[bytes] = []
    async for chunk in resp.body_iterator:
        if isinstance(chunk, str):
            chunk = chunk.encode()
        if chunk:
            chunks.append(chunk)
    joined = b"".join(chunks)
    assert joined == b"chunk-1;chunk-2;chunk-3;"
    assert len(chunks) >= 2, (
        f"proxy coalesced {len(chunks)} chunk(s) — indicates upstream "
        f"was buffered before forwarding"
    )


# ---------------------------------------------------------------------------
# Connection reuse (the "one segment at a time" bug)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proxy_shares_httpx_client_across_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: MP4 playback in a browser opens many short-lived
    Range requests; each one used to spawn a fresh ``AsyncClient``
    and discard the warm TCP connection to the node. The shared
    client must survive across proxy calls so httpx's pool can hand
    out a keep-alive connection instead of doing a new handshake.
    """

    call_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(
            200,
            headers={"content-type": "video/mp4", "content-length": "3"},
            content=_streaming_body(b"abc"),
        )

    _install_mock_transport(monkeypatch, handler)

    # Reset explicitly so the first call constructs the client under
    # the mock transport, then subsequent calls reuse it.
    await proxy_mod._reset_shared_client()

    for _ in range(3):
        resp = await proxy_mod.proxy_get(
            "node",
            "x.mp4",
            _make_request(),
            _FakeRegistry(_FakeSession()),  # type: ignore[arg-type]
        )
        assert (await _collect_body(resp)) == b"abc"

    assert call_count == 3

    # The client identity should not change between calls.
    first = proxy_mod._shared_client
    resp = await proxy_mod.proxy_get(
        "node",
        "x.mp4",
        _make_request(),
        _FakeRegistry(_FakeSession()),  # type: ignore[arg-type]
    )
    await _collect_body(resp)
    assert proxy_mod._shared_client is first, (
        "shared client was rebuilt between requests — connection pool lost"
    )


@pytest.mark.asyncio
async def test_reset_shared_client_lets_next_call_build_fresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reset helper must actually null out the singleton so a
    subsequent proxy call constructs a fresh client. Tests rely on
    this; without it the ``httpx.AsyncClient`` monkey-patch installed
    for test N+1 is ignored in favor of the stale mock from test N.
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "video/mp4"}, content=_streaming_body(b"x")
        )

    _install_mock_transport(monkeypatch, handler)
    resp = await proxy_mod.proxy_get(
        "node",
        "x.mp4",
        _make_request(),
        _FakeRegistry(_FakeSession()),  # type: ignore[arg-type]
    )
    await _collect_body(resp)
    assert proxy_mod._shared_client is not None
    before = proxy_mod._shared_client

    await proxy_mod._reset_shared_client()
    assert proxy_mod._shared_client is None

    resp = await proxy_mod.proxy_get(
        "node",
        "x.mp4",
        _make_request(),
        _FakeRegistry(_FakeSession()),  # type: ignore[arg-type]
    )
    await _collect_body(resp)
    assert proxy_mod._shared_client is not None
    assert proxy_mod._shared_client is not before


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proxy_404_when_node_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=_streaming_body(b""))

    _install_mock_transport(monkeypatch, handler)

    with pytest.raises(HTTPException) as excinfo:
        await proxy_mod.proxy_get(
            "unknown",
            "x.mp4",
            _make_request(),
            _FakeRegistry(session=None),
        )
    assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_proxy_502_when_node_has_no_advertised_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_streaming_body(b""))

    _install_mock_transport(monkeypatch, handler)

    with pytest.raises(HTTPException) as excinfo:
        await proxy_mod.proxy_get(
            "node",
            "x.mp4",
            _make_request(),
            _FakeRegistry(_FakeSession(advertised_url=None)),  # type: ignore[arg-type]
        )
    assert excinfo.value.status_code == 502
