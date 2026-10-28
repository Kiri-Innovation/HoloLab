"""Independent, node-initiated HTTP data tunnels.

One tunnel WebSocket carries one in-flight HTTP stream.  A node maintains a
pool of 64 such sockets, which gives every browser request independent TCP
flow control while keeping the job-control WebSocket completely untouched.
The wire messages are logical HTTP request/response metadata; binary frames
are only the streamed response body.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass, field

from fastapi import HTTPException, WebSocket

_QUEUE_LIMIT = 64
_BODY_QUEUE_LIMIT = 8


@dataclass(eq=False)
class TunnelConnection:
    node_id: str
    session_id: str
    ws: WebSocket
    registry: TunnelRegistry
    response: asyncio.Future[dict] | None = None
    body: asyncio.Queue[bytes | None] = field(
        default_factory=lambda: asyncio.Queue(maxsize=_BODY_QUEUE_LIMIT)
    )
    stream_id: str | None = None
    closed: bool = False

    async def run(self) -> None:
        """Receive response metadata and body for this single active flow."""
        try:
            while True:
                message = await self.ws.receive()
                if message.get("type") == "websocket.disconnect":
                    return
                data = message.get("bytes")
                if data is not None:
                    await self.body.put(data)
                    continue
                text = message.get("text")
                if text is None:
                    continue
                frame = json.loads(text)
                if frame.get("stream_id") != self.stream_id:
                    continue
                kind = frame.get("kind")
                if kind == "response" and self.response is not None and not self.response.done():
                    self.response.set_result(frame)
                elif kind in {"end", "error"}:
                    if kind == "error" and self.response is not None and not self.response.done():
                        self.response.set_exception(
                            RuntimeError(frame.get("detail", "node tunnel error"))
                        )
                    await self.body.put(None)
        finally:
            self.closed = True
            if self.response is not None and not self.response.done():
                self.response.set_exception(RuntimeError("node data tunnel disconnected"))
            await self.registry.detach(self)

    async def request(
        self, method: str, path: str, query: str, headers: dict[str, str]
    ) -> tuple[dict, AsyncIterator[bytes]]:
        if self.closed:
            raise RuntimeError("node data tunnel disconnected")
        self.stream_id = str(uuid.uuid4())
        self.response = asyncio.get_running_loop().create_future()
        await self.ws.send_text(
            json.dumps(
                {
                    "kind": "request",
                    "stream_id": self.stream_id,
                    "method": method,
                    "path": path,
                    "query": query,
                    "headers": headers,
                },
                separators=(",", ":"),
            )
        )
        response = await asyncio.wait_for(self.response, timeout=10)

        async def body() -> AsyncIterator[bytes]:
            completed = False
            try:
                while True:
                    chunk = await self.body.get()
                    if chunk is None:
                        completed = True
                        return
                    yield chunk
            finally:
                if not completed and not self.closed:
                    with suppress(Exception):
                        await self.ws.send_text(
                            json.dumps({"kind": "cancel", "stream_id": self.stream_id})
                        )
                await self.registry.release(self)

        return response, body()


class TunnelRegistry:
    """Checkout pool for independently backpressured node data streams."""

    def __init__(self) -> None:
        self._idle: dict[tuple[str, str], asyncio.Queue[TunnelConnection]] = {}
        self._all: set[TunnelConnection] = set()
        self._lock = asyncio.Lock()

    async def attach(self, connection: TunnelConnection) -> None:
        key = (connection.node_id, connection.session_id)
        async with self._lock:
            queue = self._idle.setdefault(key, asyncio.Queue(maxsize=_QUEUE_LIMIT))
            if queue.full():
                await connection.ws.close(code=1013, reason="node tunnel pool full")
                return
            self._all.add(connection)
            queue.put_nowait(connection)

    async def detach(self, connection: TunnelConnection) -> None:
        async with self._lock:
            self._all.discard(connection)

    async def acquire(self, node_id: str, session_id: str) -> TunnelConnection:
        queue = self._idle.get((node_id, session_id))
        if queue is None:
            raise HTTPException(status_code=503, detail="Preview tunnel is not connected")
        try:
            return await asyncio.wait_for(queue.get(), timeout=0.05)
        except asyncio.TimeoutError as exc:
            raise HTTPException(status_code=429, detail="Preview tunnel is saturated") from exc

    async def release(self, connection: TunnelConnection) -> None:
        if connection.closed:
            return
        key = (connection.node_id, connection.session_id)
        async with self._lock:
            queue = self._idle.get(key)
            if queue is not None and not queue.full():
                queue.put_nowait(connection)
