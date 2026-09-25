"""Any incoming frame from a node refreshes ``session.last_heartbeat_ts``.

Regression guard for the "gateway declares node dead mid-fanout" symptom
observed 2026-09-25 during two consecutive reruns:

    node heartbeat timeout → WS closed
      → 85+ in-flight shards flip to SYSTEM_ERROR
      "assigned compute node dropped mid-fanout"
    ~1 s later node reconnects; next batch runs cleanly

Root cause: the node's ``_heartbeat_loop`` (runtime.py:783) sends a
``heartbeat`` frame every ``HEARTBEAT_INTERVAL=15s`` through the
single ``_send_lock`` (runtime.py:184) that also serialises every
``job_log`` / ``job_progress`` / ``handle_register`` / ``job_ack`` /
``job_done`` frame. Under an 8-way fan-out with each shard flushing
logs every ``LOG_FLUSH_INTERVAL=0.5s`` (two ``_safe_send`` acquisitions
per flush) plus per-subprocess progress relays, the lock queue depth
grows enough that a heartbeat waits > ``HEARTBEAT_DEAD_SECONDS=45s``
before hitting the wire — even though the node's asyncio loop is
healthy and producing frames the whole time. The gateway sweeper
(``_heartbeat_sweeper`` at app.py:3681) sees the stale timestamp and
kills the session.

Fix: ``_dispatch_node_frame`` refreshes ``session.last_heartbeat_ts``
at the top for every frame kind, not just for ``heartbeat``. Any frame
arriving is proof that the node's send loop is servicing something —
which is the property the sweeper actually cares about. A genuinely
silent node (loop stuck, WS half-dead) still hits the deadline
because no frame of any kind arrives.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from hololab.gateway.app import _dispatch_node_frame
from hololab.gateway.registry import NodeSession
from hololab.protocol.messages import GpuInfo, Heartbeat, NodeMetrics


def _make_session(*, last_hb_ts: float) -> NodeSession:
    ws = MagicMock()
    ws.send_text = AsyncMock()
    s = NodeSession(
        node_id="n1",
        session_id="sess-1",
        node_name="n1-name",
        ws=ws,
        advertised_url=None,
        gpu=GpuInfo(),
        packs=[],
        protocol_v=1,
        workspace_root="/tmp/ws-not-used-in-test",
    )
    s.last_heartbeat_ts = last_hb_ts
    return s


def _make_stub_app() -> Any:
    """Minimal FastAPI-shaped stub — dispatch reads app.state.metrics for
    ``node_metrics`` frames but nothing else at this level."""

    app = MagicMock()
    metrics = MagicMock()
    metrics.record = MagicMock(side_effect=lambda _nid, payload: payload)
    app.state = MagicMock()
    app.state.metrics = metrics
    return app


class _StubHub:
    """No-op FrontendHub stub; ``broadcast`` swallows the frame."""

    def broadcast(self, _frame: str) -> None:
        pass


@pytest.mark.asyncio
async def test_non_heartbeat_frame_refreshes_liveness_timestamp() -> None:
    """The whole point: a ``node_metrics`` frame (or any other kind)
    must reset ``last_heartbeat_ts`` so a busy node's heartbeat-timing
    doesn't rely on the ``heartbeat`` frame winning the ``_send_lock``
    race against bulk log/progress traffic."""

    stale = time.time() - 100.0  # well past HEARTBEAT_DEAD_SECONDS=45
    session = _make_session(last_hb_ts=stale)
    app = _make_stub_app()
    metrics_payload = NodeMetrics(ts=time.time())

    before = time.time()
    await _dispatch_node_frame(
        "node_metrics",
        metrics_payload,
        session,
        store=MagicMock(),
        handles=MagicMock(),
        hub=_StubHub(),
        registry=MagicMock(),
        logs=MagicMock(),
        app=app,
    )
    after = time.time()

    assert before <= session.last_heartbeat_ts <= after, (
        "non-heartbeat frame must refresh the liveness timestamp"
    )


@pytest.mark.asyncio
async def test_heartbeat_frame_still_refreshes_liveness_timestamp() -> None:
    """Belt-and-braces: the explicit ``heartbeat`` frame remains a valid
    liveness signal. This is redundant with the pre-fix code path but
    guards the ``heartbeat`` case is not accidentally routed around the
    top-level tickle by a future refactor."""

    stale = time.time() - 100.0
    session = _make_session(last_hb_ts=stale)
    app = _make_stub_app()

    before = time.time()
    await _dispatch_node_frame(
        "heartbeat",
        Heartbeat(running_jobs=[]),
        session,
        store=MagicMock(),
        handles=MagicMock(),
        hub=_StubHub(),
        registry=MagicMock(),
        logs=MagicMock(),
        app=app,
    )
    after = time.time()

    assert before <= session.last_heartbeat_ts <= after


@pytest.mark.asyncio
async def test_unknown_frame_kind_still_refreshes_liveness_timestamp() -> None:
    """The tickle sits before the kind-dispatch tree so even an
    unhandled/legacy frame kind refreshes liveness. A node that keeps
    talking is alive, whether or not the gateway knows what to do with
    the specific frame."""

    stale = time.time() - 100.0
    session = _make_session(last_hb_ts=stale)
    app = _make_stub_app()

    before = time.time()
    await _dispatch_node_frame(
        "some_future_kind_the_gateway_doesnt_know",
        MagicMock(),  # payload shape doesn't matter — the dispatcher no-ops
        session,
        store=MagicMock(),
        handles=MagicMock(),
        hub=_StubHub(),
        registry=MagicMock(),
        logs=MagicMock(),
        app=app,
    )
    after = time.time()

    assert before <= session.last_heartbeat_ts <= after
