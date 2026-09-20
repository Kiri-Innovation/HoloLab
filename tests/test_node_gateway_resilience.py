"""Robustness of the node → gateway control channel across gateway restarts.

Backstory: a fan-out of 101 shards fired during a gateway bounce lost 6
shards to ``input handle resolution failed: gateway did not respond to
locate ...``. The failure sat in ``_locate_handle`` — a one-shot
request/response with a 30s timeout and no retry. If the WS dropped
mid-flight (which is exactly what a gateway restart does) the pending
future never resolved and the shard was marked permanently failed.

These tests cover the retry loop that replaced that one-shot: the
happy path, retry on transient failure, waiting for the reconnect
handshake, and the exhaustion path (surfaced with a human-readable
"gateway unreachable" message so operators can distinguish that from
a real business error).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest
from websockets.exceptions import ConnectionClosed, ConnectionClosedError

from hololab.node import runtime as runtime_mod
from hololab.node.runtime import NodeRuntime, _HandleResolutionError
from hololab.protocol.messages import HandleLocateResp


def _fresh_runtime() -> NodeRuntime:
    """Bare NodeRuntime with just enough state for ``_locate_handle``."""

    r = NodeRuntime.__new__(NodeRuntime)
    r._config = MagicMock()
    r._ws = None
    r._ws_ready = asyncio.Event()
    r._pending_locates = {}
    r._send_lock = asyncio.Lock()
    r._protocol_v = 1
    return r


@pytest.fixture(autouse=True)
def _fast_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shorten the retry timings so the tests don't stall the suite.

    The real constants are tuned for wallclock behavior across a live
    gateway restart (~30s total budget). For unit tests we shrink both
    the per-attempt timeout and the backoff sleeps to keep every case
    under 1s.
    """

    monkeypatch.setattr(runtime_mod, "_LOCATE_ATTEMPT_TIMEOUT_S", 0.05)
    monkeypatch.setattr(runtime_mod, "_LOCATE_BACKOFF_START_S", 0.01)
    monkeypatch.setattr(runtime_mod, "_LOCATE_BACKOFF_CAP_S", 0.02)


@pytest.mark.asyncio
async def test_locate_success_first_try() -> None:
    """Happy path: gateway responds immediately."""

    r = _fresh_runtime()
    r._ws_ready.set()

    async def fake_send(kind: str, payload: Any) -> None:
        assert kind == "handle_locate_req"
        fut = r._pending_locates[payload.handle_id]
        fut.set_result(HandleLocateResp(handle_id=payload.handle_id, local_path="/tmp/x"))

    r._send = fake_send  # type: ignore[assignment]

    resp = await r._locate_handle("h1")
    assert resp.local_path == "/tmp/x"


@pytest.mark.asyncio
async def test_locate_retries_across_transient_send_failure() -> None:
    """One send raises ``ConnectionClosed`` (gateway mid-restart); the next
    send succeeds. The retry loop must survive that and return the
    eventual answer instead of surfacing the transient error.
    """

    r = _fresh_runtime()
    r._ws_ready.set()

    call_count = 0

    async def fake_send(kind: str, payload: Any) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ConnectionClosedError(None, None)
        fut = r._pending_locates[payload.handle_id]
        fut.set_result(HandleLocateResp(handle_id=payload.handle_id, local_path="/tmp/x"))

    r._send = fake_send  # type: ignore[assignment]

    resp = await r._locate_handle("h1")
    assert resp.local_path == "/tmp/x"
    assert call_count == 2


@pytest.mark.asyncio
async def test_locate_retries_when_response_lost_to_disconnect() -> None:
    """Send succeeded but the response never arrived (gateway crashed
    after receiving the request). The per-attempt wait times out and
    the loop retries on the next attempt.
    """

    r = _fresh_runtime()
    r._ws_ready.set()

    call_count = 0

    async def fake_send(kind: str, payload: Any) -> None:
        nonlocal call_count
        call_count += 1
        # First attempt: don't resolve the future — the wait_for will
        # trip and we retry.
        if call_count == 1:
            return
        fut = r._pending_locates[payload.handle_id]
        fut.set_result(HandleLocateResp(handle_id=payload.handle_id, local_path="/tmp/x"))

    r._send = fake_send  # type: ignore[assignment]

    resp = await r._locate_handle("h1")
    assert resp.local_path == "/tmp/x"
    assert call_count == 2


@pytest.mark.asyncio
async def test_locate_waits_for_ws_ready() -> None:
    """When the WS isn't yet ready (reconnect handshake in progress),
    the retry loop blocks until it is, then proceeds. This is the
    "started fan-out during a gateway bounce" scenario.
    """

    r = _fresh_runtime()
    # Not yet set — simulate mid-reconnect.

    async def flip_ready_after_delay() -> None:
        await asyncio.sleep(0.02)
        r._ws_ready.set()

    async def fake_send(kind: str, payload: Any) -> None:
        fut = r._pending_locates[payload.handle_id]
        fut.set_result(HandleLocateResp(handle_id=payload.handle_id, local_path="/tmp/x"))

    r._send = fake_send  # type: ignore[assignment]

    flipper = asyncio.create_task(flip_ready_after_delay())
    try:
        resp = await r._locate_handle("h1")
    finally:
        await flipper
    assert resp.local_path == "/tmp/x"


@pytest.mark.asyncio
async def test_locate_gives_up_after_max_attempts_with_readable_error() -> None:
    """Gateway stays unreachable through every retry. The error message
    names *gateway unreachable* (not the older vague *did not respond*)
    so an operator scanning fail_message in the job log can tell this
    apart from a real "unknown handle" error.
    """

    r = _fresh_runtime()
    r._ws_ready.set()

    call_count = 0

    async def fake_send(kind: str, payload: Any) -> None:
        nonlocal call_count
        call_count += 1
        raise ConnectionClosedError(None, None)

    r._send = fake_send  # type: ignore[assignment]

    with pytest.raises(_HandleResolutionError) as excinfo:
        await r._locate_handle("h1")

    msg = str(excinfo.value)
    assert "gateway unreachable" in msg
    assert "h1" in msg
    assert "gave up after" in msg
    # Every attempt should have called send once — proves we retried
    # up to the cap rather than bailing on the first failure.
    assert call_count == runtime_mod._LOCATE_MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_locate_does_not_retry_on_business_error() -> None:
    """A ``not_found=True`` response from the gateway is a real answer,
    not a transport hiccup. The retry loop must return it verbatim so
    the outer ``_resolve_input_handles`` can raise "unknown handle …"
    (which the user *does* need to see, unlike the transport error we
    used to conflate it with).
    """

    r = _fresh_runtime()
    r._ws_ready.set()

    call_count = 0

    async def fake_send(kind: str, payload: Any) -> None:
        nonlocal call_count
        call_count += 1
        fut = r._pending_locates[payload.handle_id]
        fut.set_result(HandleLocateResp(handle_id=payload.handle_id, not_found=True))

    r._send = fake_send  # type: ignore[assignment]

    resp = await r._locate_handle("h1")
    assert resp.not_found is True
    # Exactly one send: business errors don't burn the retry budget.
    assert call_count == 1


@pytest.mark.asyncio
async def test_ws_ready_gate_clears_on_transient_reconnect() -> None:
    """Simulates the reconnect flow: session ends (``_ws_ready`` cleared),
    reconnect handshake fires (``_ws_ready`` set again). A locate that
    fires mid-window blocks until the new session is up rather than
    trying to write on a dead socket.
    """

    r = _fresh_runtime()
    # First session was live but died.
    r._ws_ready.set()
    r._ws_ready.clear()

    async def bring_gateway_back() -> None:
        # Model the gateway's "up in ~50ms" behavior.
        await asyncio.sleep(0.02)
        r._ws_ready.set()

    sent_at_ready = False

    async def fake_send(kind: str, payload: Any) -> None:
        nonlocal sent_at_ready
        sent_at_ready = r._ws_ready.is_set()
        fut = r._pending_locates[payload.handle_id]
        fut.set_result(HandleLocateResp(handle_id=payload.handle_id, local_path="/tmp/x"))

    r._send = fake_send  # type: ignore[assignment]

    flipper = asyncio.create_task(bring_gateway_back())
    try:
        resp = await r._locate_handle("h1")
    finally:
        await flipper
    assert resp.local_path == "/tmp/x"
    assert sent_at_ready, "send must only fire once ws_ready is set"


@pytest.mark.asyncio
async def test_pending_locates_is_cleared_between_attempts() -> None:
    """After every attempt (success or failure) the per-handle future
    entry is removed from ``_pending_locates`` so a stray late response
    from a prior session doesn't overwrite a subsequent attempt's
    future by referencing a stale dictionary slot.
    """

    r = _fresh_runtime()
    r._ws_ready.set()

    async def fake_send(kind: str, payload: Any) -> None:
        raise ConnectionClosedError(None, None)

    r._send = fake_send  # type: ignore[assignment]

    with pytest.raises(_HandleResolutionError):
        await r._locate_handle("h1")

    assert "h1" not in r._pending_locates


def test_connection_closed_is_importable() -> None:
    """Belt-and-braces: the module we depend on for retryable error
    classification is the one we think it is (websockets renamed
    the parent class a couple of releases back)."""

    assert issubclass(ConnectionClosedError, ConnectionClosed)
