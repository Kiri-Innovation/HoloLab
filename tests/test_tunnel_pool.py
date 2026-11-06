from __future__ import annotations

import pytest

from hololab.gateway.tunnel import TunnelConnection, TunnelRegistry


class _Ws:
    async def close(self, *args, **kwargs):
        return None


@pytest.mark.asyncio
async def test_disconnect_removes_stale_connection_from_idle_pool() -> None:
    registry = TunnelRegistry()
    first = TunnelConnection("n", "s", _Ws(), registry)  # type: ignore[arg-type]
    second = TunnelConnection("n", "s", _Ws(), registry)  # type: ignore[arg-type]
    await registry.attach(first)
    await registry.attach(second)

    first.closed = True
    await registry.detach(first)
    acquired = await registry.acquire("n", "s")
    assert acquired is second


@pytest.mark.asyncio
async def test_reconnect_replaces_every_dead_pool_slot() -> None:
    registry = TunnelRegistry()
    stale = [TunnelConnection("n", "s", _Ws(), registry) for _ in range(64)]  # type: ignore[arg-type]
    for connection in stale:
        await registry.attach(connection)
        connection.closed = True
        await registry.detach(connection)

    fresh = TunnelConnection("n", "s", _Ws(), registry)  # type: ignore[arg-type]
    await registry.attach(fresh)
    assert await registry.acquire("n", "s") is fresh


@pytest.mark.asyncio
async def test_rejection_before_headers_cannot_end_the_next_response() -> None:
    import asyncio
    import json
    from contextlib import suppress

    class Socket(_Ws):
        def __init__(self):
            self.incoming = asyncio.Queue()
            self.sent = asyncio.Queue()

        async def receive(self):
            return await self.incoming.get()

        async def send_text(self, text):
            await self.sent.put(json.loads(text))

        async def frame(self, kind, stream_id, **kwargs):
            await self.incoming.put(
                {"text": json.dumps({"kind": kind, "stream_id": stream_id, **kwargs})}
            )

    registry = TunnelRegistry()
    socket = Socket()
    connection = TunnelConnection("n", "s", socket, registry)  # type: ignore[arg-type]
    await registry.attach(connection)
    await registry.acquire("n", "s")
    receiver = asyncio.create_task(connection.run())
    try:
        rejected = asyncio.create_task(connection.request("GET", "/w//model", "", {}))
        request = await socket.sent.get()
        await socket.frame("error", request["stream_id"], detail="forbidden request")
        with pytest.raises(RuntimeError, match="forbidden request"):
            await rejected
        await registry.release(connection)
        assert await registry.acquire("n", "s") is connection
        accepted = asyncio.create_task(connection.request("GET", "/w/model", "", {}))
        request = await socket.sent.get()
        await socket.frame("response", request["stream_id"], status=200, headers={})
        await socket.incoming.put({"bytes": b"correct body"})
        await socket.frame("end", request["stream_id"])
        _, body = await accepted
        assert b"".join([chunk async for chunk in body]) == b"correct body"
    finally:
        receiver.cancel()
        with suppress(asyncio.CancelledError):
            await receiver


@pytest.mark.asyncio
async def test_release_rechecks_closed_state_after_waiting_for_pool_lock() -> None:
    import asyncio

    registry = TunnelRegistry()
    connection = TunnelConnection("n", "s", _Ws(), registry)  # type: ignore[arg-type]
    await registry.attach(connection)
    await registry.acquire("n", "s")
    await registry._lock.acquire()
    release = asyncio.create_task(registry.release(connection))
    await asyncio.sleep(0)
    connection.closed = True
    registry._lock.release()
    await release
    assert registry._idle[("n", "s")].empty()
