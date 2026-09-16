"""Regression coverage for the frontend fanout hub.

The class of bug we are guarding against here is the day-0 unhashable-
Subscriber crash: ``FrontendHub._subs`` is a set, and ``@dataclass`` (without
``eq=False``) removes ``__hash__``. Every real frontend WebSocket connection
hit ``TypeError: unhashable type: 'Subscriber'`` on ``add()`` and died. The
pack-init/protocol/state-machine tests never exercised a real socket, so it
slipped through — hence this file.

The unit test covers the hashability + set membership contract directly. The
end-to-end test connects a real WebSocket against the live FastAPI app and
asserts that a broadcast frame actually arrives.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from hololab.gateway.app import create_app
from hololab.gateway.hub import FrontendHub, Subscriber


def test_subscriber_is_hashable_and_lives_in_a_set() -> None:
    """The regression: Subscriber must be usable as a set element."""

    sub_a = Subscriber(ws=MagicMock())
    sub_b = Subscriber(ws=MagicMock())

    # This is exactly what FrontendHub.add does — the bug crashed here.
    bucket: set[Subscriber] = set()
    bucket.add(sub_a)
    bucket.add(sub_b)
    assert len(bucket) == 2

    # Identity equality: even with the same wrapped mock, two instances are
    # distinct subscribers (each represents one socket).
    same_ws = MagicMock()
    sub_c = Subscriber(ws=same_ws)
    sub_d = Subscriber(ws=same_ws)
    assert sub_c != sub_d
    assert hash(sub_c) != hash(sub_d)


def test_hub_add_and_remove_roundtrip() -> None:
    """Sanity: add() / remove() work with the hashable Subscriber."""

    hub = FrontendHub()
    sub = hub.add(MagicMock())
    assert sub in hub._subs
    hub.remove(sub)
    assert sub not in hub._subs


def test_frontend_ws_connects_and_receives_broadcast(tmp_path: Path) -> None:
    """End-to-end: real WS connect through the app + broadcast delivered.

    This is the regression that would have caught the shipped bug. Before the
    fix, ``client.websocket_connect("/ws/frontend")`` triggered the
    unhashable-Subscriber crash inside ``add()`` and the connection died
    right after the 101 handshake.
    """

    app = create_app(db_path=tmp_path / "test.sqlite")

    with TestClient(app) as client, client.websocket_connect("/ws/frontend") as ws:
        # The gateway broadcasts through its hub instance stashed on app.state
        # during startup. Broadcast a small frame and verify the client sees it.
        frame = json.dumps(
            {
                "v": 1,
                "id": "test",
                "kind": "job_update",
                "payload": {
                    "job_id": "j1",
                    "state": "pending",
                    "workflow_id": "w1",
                    "algorithm_name": "demo-echo",
                    "algorithm_version": "0.1.0",
                    "progress": None,
                    "fail": None,
                },
                "ts": 0.0,
            }
        )
        app.state.hub.broadcast(frame)

        received = ws.receive_text()
        assert "job_update" in received
        assert "demo-echo" in received
