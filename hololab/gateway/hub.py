"""Frontend fanout — one bounded queue per subscribed browser.

Slow consumers must not block fast ones or the gateway itself. Each subscriber
gets a bounded ``asyncio.Queue``; overflow drops the oldest queued message.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from fastapi import WebSocket

from hololab.logging import get_logger

log = get_logger("gateway.hub")

# Bound per-subscriber queue. Set high enough for reasonable log bursts but low
# enough that a stuck browser tab can't OOM us.
_MAX_QUEUED = 512


@dataclass(eq=False)
class Subscriber:
    """One connected frontend WebSocket.

    ``eq=False`` keeps Python's default identity-based ``__eq__`` and
    ``__hash__``, which is what we want: each Subscriber represents one live
    socket, and two instances are equal iff they are the same object. Without
    this, ``@dataclass`` synthesizes structural ``__eq__`` and silently sets
    ``__hash__ = None``, so ``FrontendHub._subs`` (a set) would refuse to hold
    it and every frontend WS handler would raise ``TypeError`` on connect.
    """

    ws: WebSocket
    queue: asyncio.Queue[str] = field(default_factory=lambda: asyncio.Queue(maxsize=_MAX_QUEUED))
    dropped: int = 0


class FrontendHub:
    """Broadcast wire frames to all connected frontends, with backpressure."""

    def __init__(self) -> None:
        self._subs: set[Subscriber] = set()

    def add(self, ws: WebSocket) -> Subscriber:
        sub = Subscriber(ws=ws)
        self._subs.add(sub)
        return sub

    def remove(self, sub: Subscriber) -> None:
        self._subs.discard(sub)

    def broadcast(self, frame: str) -> None:
        """Enqueue ``frame`` to every subscriber's outbox. Non-blocking."""

        for sub in list(self._subs):
            try:
                sub.queue.put_nowait(frame)
            except asyncio.QueueFull:
                # Drop oldest, enqueue new. This keeps the tail (freshest logs)
                # rather than the head.
                try:
                    _ = sub.queue.get_nowait()
                    sub.dropped += 1
                except asyncio.QueueEmpty:
                    pass
                try:
                    sub.queue.put_nowait(frame)
                except asyncio.QueueFull:  # pragma: no cover - shouldn't happen
                    log.warning("frontend queue still full after drop")

    async def pump(self, sub: Subscriber) -> None:
        """Blocking coroutine: send each queued frame over ``sub.ws``.

        Terminates by exception on socket close.
        """

        while True:
            frame = await sub.queue.get()
            await sub.ws.send_text(frame)
