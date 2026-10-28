"""Node side of the reverse, read-only HTTP preview tunnel."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from urllib.parse import quote

import httpx
import websockets

TUNNEL_CONNECTIONS = 64
_BODY_CHUNK_SIZE = 256 * 1024
_FORWARD_HEADERS = frozenset({"range", "if-none-match", "if-modified-since"})
_RESPONSE_HEADERS = frozenset(
    {
        "content-type",
        "content-length",
        "content-range",
        "etag",
        "last-modified",
        "cache-control",
        "accept-ranges",
    }
)


def _allowed_path(path: str) -> bool:
    """Allow only the file-server's public, read-only routes."""
    if not path.startswith("/") or "\\" in path:
        return False
    pieces = path.split("/")
    if any(piece in {"", ".", ".."} for piece in pieces[1:]):
        return False
    return path.startswith(
        ("/_thumb/", "/_preview/", "/_pack/", "/_scoped_pack/")
    ) or not path.startswith("/_")


class FileTunnelClient:
    """Maintains independently backpressured outbound streams to the gateway."""

    def __init__(self, *, gateway_url: str, file_server_port: int) -> None:
        self._gateway_url = (
            gateway_url.rstrip("/").replace("http://", "ws://").replace("https://", "wss://")
        )
        self._origin = f"http://127.0.0.1:{file_server_port}"
        self._tasks: list[asyncio.Task[None]] = []
        self._client: httpx.AsyncClient | None = None

    async def start(self, *, node_id: str, session_id: str, token: str) -> None:
        await self.stop()
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5, read=None, write=30, pool=5),
            limits=httpx.Limits(max_connections=TUNNEL_CONNECTIONS, max_keepalive_connections=32),
        )
        for index in range(TUNNEL_CONNECTIONS):
            task = asyncio.create_task(
                self._worker(node_id, session_id, token, index), name=f"hololab-file-tunnel-{index}"
            )
            self._tasks.append(task)

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _worker(self, node_id: str, session_id: str, token: str, index: int) -> None:
        url = f"{self._gateway_url}/ws/file-tunnel?node_id={quote(node_id)}&session_id={quote(session_id)}&token={quote(token)}"
        while True:
            try:
                # Preview bodies (PNG/JPEG/MP4) are already compressed.  The
                # websocket client's default permessage-deflate would spend a
                # CPU pass compressing every 64 KiB body frame, and another
                # pass in the gateway, for effectively no wire saving.  On an
                # all-in-one node that starves this event loop badly enough to
                # look like a one-stream-at-a-time tunnel.  Do not offer the
                # extension on this data-only socket; the control websocket
                # retains its existing compression behaviour.
                async with websockets.connect(
                    url, ping_interval=20, max_size=None, compression=None
                ) as ws:
                    async for raw in ws:
                        if not isinstance(raw, str):
                            continue
                        frame = json.loads(raw)
                        if frame.get("kind") == "request":
                            await self._serve(ws, frame)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(min(1 + index / TUNNEL_CONNECTIONS, 2))

    async def _serve(self, ws, frame: dict) -> None:
        stream_id = frame.get("stream_id")
        method = frame.get("method")
        path = frame.get("path")
        query = frame.get("query", "")
        if method not in {"GET", "HEAD"} or not isinstance(path, str) or not _allowed_path(path):
            await ws.send(
                json.dumps({"kind": "error", "stream_id": stream_id, "detail": "forbidden request"})
            )
            return
        if not isinstance(query, str) or "#" in query:
            await ws.send(
                json.dumps({"kind": "error", "stream_id": stream_id, "detail": "invalid query"})
            )
            return
        headers = {
            str(k): str(v)
            for k, v in (frame.get("headers") or {}).items()
            if str(k).lower() in _FORWARD_HEADERS
        }
        assert self._client is not None
        target = self._origin + path + (f"?{query}" if query else "")
        try:
            async with self._client.stream(method, target, headers=headers) as response:
                response_headers = {
                    key: value
                    for key, value in response.headers.items()
                    if key.lower() in _RESPONSE_HEADERS
                }
                await ws.send(
                    json.dumps(
                        {
                            "kind": "response",
                            "stream_id": stream_id,
                            "status": response.status_code,
                            "headers": response_headers,
                        }
                    )
                )

                async def pump() -> None:
                    # A 2.7 MiB thumbnail otherwise becomes roughly forty
                    # 64 KiB WebSocket messages.  Fewer, bounded 256 KiB
                    # frames materially reduce masking/ASGI scheduling cost
                    # without turning a response into a buffered blob.  The
                    # gateway queue remains the per-flow backpressure point.
                    async for chunk in response.aiter_raw(chunk_size=_BODY_CHUNK_SIZE):
                        await ws.send(chunk)

                writer = asyncio.create_task(pump())
                reader = asyncio.create_task(ws.recv())
                cancelled = False
                while not writer.done():
                    done, _ = await asyncio.wait(
                        {writer, reader}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if writer in done:
                        break
                    incoming = reader.result()
                    if isinstance(incoming, str):
                        control = json.loads(incoming)
                        if (
                            control.get("kind") == "cancel"
                            and control.get("stream_id") == stream_id
                        ):
                            writer.cancel()
                            with suppress(asyncio.CancelledError):
                                await writer
                            cancelled = True
                            break
                    reader = asyncio.create_task(ws.recv())
                if not reader.done():
                    reader.cancel()
                    with suppress(asyncio.CancelledError):
                        await reader
                if not cancelled:
                    await writer
                await ws.send(json.dumps({"kind": "end", "stream_id": stream_id}))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await ws.send(json.dumps({"kind": "error", "stream_id": stream_id, "detail": str(exc)}))
