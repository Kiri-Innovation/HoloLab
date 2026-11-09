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
_ACQUIRE_TIMEOUT_S = 5.0


@dataclass(eq=False)
class TunnelConnection:
    node_id: str
    session_id: str
    ws: WebSocket
    registry: TunnelRegistry
    response: asyncio.Future[dict] | None = None
    body: asyncio.Queue[bytes | dict | Exception] = field(
        default_factory=lambda: asyncio.Queue(maxsize=_BODY_QUEUE_LIMIT)
    )
    stream_id: str | None = None
    closed: bool = False
    terminal_received: bool = False

    def _fail_body(self) -> None:
        # Wake a blocked consumer, and unblock a producer waiting on a full
        # queue. This socket is retired, so none of its buffered bytes survive.
        while not self.body.empty():
            self.body.get_nowait()
        self.body.put_nowait(RuntimeError("node data tunnel disconnected"))

    async def abort(self) -> None:
        """Never reuse a socket whose previous response wasn't fully consumed.

        Binary frames have no stream id. A cancel message is not an ACK: old
        bytes/end may still be in flight and would corrupt the next request.
        Closing this one socket gives the node an unambiguous stream boundary.
        """
        self.closed = True
        self._fail_body()
        await self.registry.detach(self)
        with suppress(Exception):
            await self.ws.close(code=1011, reason="incomplete HTTP stream")

    async def run(self) -> None:
        """Receive response metadata and body for this single active flow."""
        try:
            while not self.closed:
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
                        # No response/body iterator exists for a pre-header rejection.
                        # A queued EOF would truncate the next request on this socket.
                        continue
                    await self.body.put(frame)
                    self.terminal_received = True
        finally:
            self.closed = True
            if self.response is not None and not self.response.done():
                self.response.set_exception(RuntimeError("node data tunnel disconnected"))
            if not self.terminal_received:
                self._fail_body()
            await self.registry.detach(self)

    async def request(
        self, method: str, path: str, query: str, headers: dict[str, str]
    ) -> tuple[dict, AsyncIterator[bytes]]:
        if self.closed:
            raise RuntimeError("node data tunnel disconnected")
        self.stream_id = str(uuid.uuid4())
        self.terminal_received = False
        self.response = asyncio.get_running_loop().create_future()
        try:
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
        except BaseException:
            # A clean pre-header rejection has no queued body and can be
            # reused. Timeout/cancellation/send failure have no such boundary.
            rejected = (
                self.response.done()
                and not self.response.cancelled()
                and self.response.exception() is not None
                and not self.closed
            )
            if not rejected:
                self.response.cancel()
                await self.abort()
            raise

        response_headers = {k.lower(): v for k, v in response.get("headers", {}).items()}
        # HEAD/304 can carry the GET representation's length without a body.
        no_body = method == "HEAD" or int(response["status"]) in (204, 304)
        expected = 0 if no_body else response_headers.get("content-length")

        async def body() -> AsyncIterator[bytes]:
            completed = False
            received = 0
            try:
                expected_length = int(expected) if expected is not None else None
                while True:
                    chunk = await self.body.get()
                    if isinstance(chunk, Exception):
                        raise chunk
                    if isinstance(chunk, dict):
                        if chunk["kind"] == "error":
                            raise RuntimeError(chunk.get("detail", "node tunnel error"))
                        sent = chunk.get("bytes_sent")  # Optional for older nodes.
                        if sent is not None and sent != received:
                            raise RuntimeError(f"tunnel end length mismatch: {received} != {sent}")
                        if expected_length is not None and received != expected_length:
                            raise RuntimeError(
                                f"tunnel content-length mismatch: {received} != {expected_length}"
                            )
                        completed = True
                        return
                    received += len(chunk)
                    if expected_length is not None and received > expected_length:
                        raise RuntimeError(
                            f"tunnel body exceeds content-length: {received} > {expected_length}"
                        )
                    yield chunk
            finally:
                if completed:
                    await self.registry.release(self)
                else:
                    await self.abort()

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
        """Remove a dead connection from both bookkeeping *and* checkout pool.

        Leaving it in ``asyncio.Queue`` is catastrophic: reconnects then fill
        the pool with dead sockets and proxy requests acquire those stale
        objects, producing 502s while one surviving connection serialises all
        successful traffic.
        """
        async with self._lock:
            self._all.discard(connection)
            key = (connection.node_id, connection.session_id)
            queue = self._idle.get(key)
            if queue is None:
                return
            survivors: list[TunnelConnection] = []
            while True:
                try:
                    candidate = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if candidate is not connection and not candidate.closed:
                    survivors.append(candidate)
            for candidate in survivors:
                queue.put_nowait(candidate)

    async def acquire(self, node_id: str, session_id: str) -> TunnelConnection:
        queue = self._idle.get((node_id, session_id))
        if queue is None:
            raise HTTPException(status_code=503, detail="Preview tunnel is not connected")
        try:
            # A reconnecting node needs a short warm-up window to refill its
            # 64 independent streams.  Returning 429 after 50 ms turns that
            # harmless race into a thundering herd; after five seconds it is
            # a genuine saturation signal.
            return await asyncio.wait_for(queue.get(), timeout=_ACQUIRE_TIMEOUT_S)
        except asyncio.TimeoutError as exc:
            raise HTTPException(status_code=429, detail="Preview tunnel is saturated") from exc

    async def release(self, connection: TunnelConnection) -> None:
        key = (connection.node_id, connection.session_id)
        async with self._lock:
            # A disconnect may have detached the socket while we waited for this lock.
            if connection.closed:
                return
            queue = self._idle.get(key)
            if queue is not None and not queue.full():
                queue.put_nowait(connection)
