"""Node-side handle-locate cache + single-flight coalescing.

Same handle_id is queried by N shards during a fan-out — every shard's
input list carries the shared upstream. Without a cache each shard
burns a gateway round-trip, and a momentarily-unhealthy gateway takes
N concurrent retry storms instead of one. These tests pin the cache's
contract: fast reuse, single-flight coalescing, TTL expiry, and no
caching of transient business errors (``not_found``).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from hololab.node import runtime as runtime_mod
from hololab.node.runtime import NodeRuntime
from hololab.protocol.messages import HandleLocateResp


def _fresh_runtime() -> NodeRuntime:
    r = NodeRuntime.__new__(NodeRuntime)
    r._config = MagicMock()
    r._ws = None
    r._ws_ready = asyncio.Event()
    r._ws_ready.set()
    r._pending_locates = {}
    r._locate_cache = {}
    r._locate_inflight = {}
    r._send_lock = asyncio.Lock()
    r._protocol_v = 1
    return r


@pytest.fixture(autouse=True)
def _fast_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_mod, "_LOCATE_ATTEMPT_TIMEOUT_S", 0.05)
    monkeypatch.setattr(runtime_mod, "_LOCATE_BACKOFF_START_S", 0.01)
    monkeypatch.setattr(runtime_mod, "_LOCATE_BACKOFF_CAP_S", 0.02)


@pytest.mark.asyncio
async def test_second_lookup_hits_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """One handle looked up twice → exactly one wire request."""

    r = _fresh_runtime()
    calls = 0

    async def fake_send(kind: str, payload: Any) -> None:
        nonlocal calls
        calls += 1
        fut = r._pending_locates[payload.handle_id]
        fut.set_result(HandleLocateResp(handle_id=payload.handle_id, local_path="/tmp/x"))

    r._send = fake_send  # type: ignore[assignment]

    a = await r._locate_handle_cached("h1")
    b = await r._locate_handle_cached("h1")
    assert a.local_path == b.local_path == "/tmp/x"
    assert calls == 1, "cache MUST prevent the second wire request"


@pytest.mark.asyncio
async def test_concurrent_lookups_coalesce_into_one_request() -> None:
    """N shards asking for the same handle at the same instant share
    one round-trip — the single-flight map dedupes them so a fan-out
    doesn't storm the gateway with N identical requests.
    """

    r = _fresh_runtime()
    calls = 0
    gate = asyncio.Event()

    async def fake_send(kind: str, payload: Any) -> None:
        nonlocal calls
        calls += 1
        # Wait until the harness has fired all N callers before
        # resolving — otherwise a fast responder would race the
        # subsequent await calls into their own attempts.
        await gate.wait()
        fut = r._pending_locates[payload.handle_id]
        fut.set_result(HandleLocateResp(handle_id=payload.handle_id, local_path="/tmp/x"))

    r._send = fake_send  # type: ignore[assignment]

    tasks = [asyncio.create_task(r._locate_handle_cached("h1")) for _ in range(5)]
    # Give asyncio a tick to admit all five callers into
    # ``_locate_handle_cached`` and consult the in-flight map.
    await asyncio.sleep(0.005)
    gate.set()
    results = await asyncio.gather(*tasks)
    assert all(rp.local_path == "/tmp/x" for rp in results)
    assert calls == 1, "single-flight MUST coalesce concurrent asks"


@pytest.mark.asyncio
async def test_not_found_is_not_cached() -> None:
    """Business ``not_found`` may flip once a producer registers late.
    Caching it would strand shards on a stale negative answer, so we
    always re-fetch not_found responses.
    """

    r = _fresh_runtime()
    calls = 0
    outcome = "missing"

    async def fake_send(kind: str, payload: Any) -> None:
        nonlocal calls
        calls += 1
        fut = r._pending_locates[payload.handle_id]
        if outcome == "missing":
            fut.set_result(HandleLocateResp(handle_id=payload.handle_id, not_found=True))
        else:
            fut.set_result(HandleLocateResp(handle_id=payload.handle_id, local_path="/tmp/x"))

    r._send = fake_send  # type: ignore[assignment]

    a = await r._locate_handle_cached("h1")
    assert a.not_found is True

    outcome = "found"
    b = await r._locate_handle_cached("h1")
    assert b.local_path == "/tmp/x"
    assert calls == 2, "not_found MUST NOT be cached; a later find should hit the wire"


@pytest.mark.asyncio
async def test_ttl_expiry_refetches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Once a cached entry passes its expiry, the next lookup goes to
    the wire again. TTL is the only invalidation trigger — handles are
    effectively immutable but the producer's advertised URL can shift.
    """

    monkeypatch.setattr(runtime_mod, "_LOCATE_CACHE_TTL_S", 0.01)

    r = _fresh_runtime()
    calls = 0

    async def fake_send(kind: str, payload: Any) -> None:
        nonlocal calls
        calls += 1
        fut = r._pending_locates[payload.handle_id]
        fut.set_result(HandleLocateResp(handle_id=payload.handle_id, local_path="/tmp/x"))

    r._send = fake_send  # type: ignore[assignment]

    await r._locate_handle_cached("h1")
    await asyncio.sleep(0.02)  # let TTL expire
    await r._locate_handle_cached("h1")
    assert calls == 2


@pytest.mark.asyncio
async def test_error_propagates_to_all_waiters() -> None:
    """When the underlying ``_locate_handle`` raises during a
    single-flight fan-in, every waiter must see the error rather than
    hanging. Otherwise a gateway outage would silently leak N tasks.
    """

    from hololab.node.runtime import _HandleResolutionError

    r = _fresh_runtime()
    gate = asyncio.Event()

    async def fake_send(kind: str, payload: Any) -> None:
        # Never resolves — the retry loop will exhaust and raise
        # _HandleResolutionError. The other waiters must see the
        # same failure through the single-flight future.
        await gate.wait()

    r._send = fake_send  # type: ignore[assignment]
    # Not a real ConnectionClosed — we just want _send to hang so the
    # retry attempts each time out. To avoid a 5-attempt wait, force
    # the retry cap to 1.
    r._ws_ready.set()

    async def caller() -> None:
        with pytest.raises(_HandleResolutionError):
            await r._locate_handle_cached("h1")

    tasks = [asyncio.create_task(caller()) for _ in range(3)]
    # Let all three enter the single-flight, then let _send resume so
    # the underlying retry loop can exhaust its attempts.
    await asyncio.sleep(0.005)
    gate.set()
    await asyncio.gather(*tasks)
