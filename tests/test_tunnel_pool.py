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
