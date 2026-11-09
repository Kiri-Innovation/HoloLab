"""Stream ownership, explicit completion and byte-count regressions."""

import asyncio
import json
from contextlib import suppress

import pytest

from hololab.gateway.tunnel import TunnelConnection, TunnelRegistry


class Socket:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.sent = asyncio.Queue()
        self.closed = False

    async def receive(self):
        return await self.incoming.get()

    async def send_text(self, text):
        await self.sent.put(json.loads(text))

    async def close(self, **kwargs):
        self.closed = True
        await self.incoming.put({"type": "websocket.disconnect"})

    async def frame(self, kind, stream_id, **kwargs):
        await self.incoming.put(
            {"text": json.dumps(dict(kind=kind, stream_id=stream_id, **kwargs))}
        )


@pytest.fixture
async def tunnel():
    registry, socket = TunnelRegistry(), Socket()
    connection = TunnelConnection("n", "s", socket, registry)
    await registry.attach(connection)
    assert await registry.acquire("n", "s") is connection
    receiver = asyncio.create_task(connection.run())
    yield connection, socket, registry
    receiver.cancel()
    with suppress(asyncio.CancelledError):
        await receiver


async def start(connection, socket, *, length=None, method="GET", status=200):
    pending = asyncio.create_task(connection.request(method, "/file", "", {}))
    request = await socket.sent.get()
    headers = {} if length is None else {"content-length": str(length)}
    await socket.frame("response", request["stream_id"], status=status, headers=headers)
    _, body = await pending
    return request["stream_id"], body


async def collect(body):
    return b"".join([chunk async for chunk in body])


async def test_aborted_body_retires_socket_instead_of_reusing_old_frames(tunnel):
    connection, socket, registry = tunnel
    _, body = await start(connection, socket, length=10)
    await socket.incoming.put({"bytes": b"first"})
    assert await anext(body) == b"first"
    await body.aclose()
    assert connection.closed
    assert socket.closed
    assert registry._idle[("n", "s")].empty()
    with pytest.raises(RuntimeError, match="disconnected"):
        await connection.request("GET", "/next", "", {})


@pytest.mark.parametrize(
    "terminal,kwargs,message",
    [
        ("end", {}, "content-length mismatch"),
        ("end", {"bytes_sent": 9}, "end length mismatch"),
        ("error", {"detail": "upstream read failed"}, "upstream read failed"),
    ],
)
async def test_partial_response_never_becomes_successful_eof(tunnel, terminal, kwargs, message):
    connection, socket, _ = tunnel
    stream, body = await start(connection, socket, length=10)
    await socket.incoming.put({"bytes": b"partial"})
    await socket.frame(terminal, stream, **kwargs)
    with pytest.raises(RuntimeError, match=message):
        await collect(body)
    assert connection.closed


async def test_disconnect_wakes_body_waiter(tunnel):
    connection, socket, _ = tunnel
    _, body = await start(connection, socket)
    await socket.incoming.put({"bytes": b"partial"})
    await socket.incoming.put({"type": "websocket.disconnect"})
    with pytest.raises(RuntimeError, match="disconnected"):
        await asyncio.wait_for(collect(body), timeout=1)


async def test_oversized_response_rejected_before_extra_bytes_are_forwarded(tunnel):
    connection, socket, _ = tunnel
    _, body = await start(connection, socket, length=2)
    await socket.incoming.put({"bytes": b"too large"})
    with pytest.raises(RuntimeError, match="exceeds content-length"):
        await collect(body)
    assert connection.closed


@pytest.mark.parametrize("with_count", [False, True])
async def test_complete_stream_reused_only_after_end_and_full_consumption(tunnel, with_count):
    connection, socket, registry = tunnel
    data = b"x" * (256 * 1024)
    for _iteration in range(2):
        stream, body = await start(connection, socket, length=len(data) * 12)

        async def produce(stream=stream):
            for _ in range(12):  # Exceed bounded queue capacity; producer must wait.
                await socket.incoming.put({"bytes": data})
            await socket.frame(
                "end", stream, **({"bytes_sent": len(data) * 12} if with_count else {})
            )

        producer = asyncio.create_task(produce())
        assert await collect(body) == data * 12
        await producer
        assert not connection.closed
        assert await registry.acquire("n", "s") is connection
        assert registry._idle[("n", "s")].empty()


@pytest.mark.parametrize("method,status", [("HEAD", 200), ("GET", 304), ("GET", 204)])
async def test_bodyless_response_does_not_require_representation_length(tunnel, method, status):
    connection, socket, _ = tunnel
    stream, body = await start(connection, socket, length=123456, method=method, status=status)
    await socket.frame("end", stream, bytes_sent=0)
    assert await collect(body) == b""


async def test_cancel_before_headers_retires_socket(tunnel):
    connection, socket, _ = tunnel
    pending = asyncio.create_task(connection.request("GET", "/file", "", {}))
    await socket.sent.get()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert connection.closed
    assert socket.closed


async def test_abort_unblocks_receiver_when_body_queue_full(tunnel):
    connection, socket, _ = tunnel
    _, body = await start(connection, socket)
    for _ in range(20):
        await socket.incoming.put({"bytes": b"chunk"})
    await anext(body)
    await body.aclose()
    await asyncio.sleep(0)
    assert connection.closed
    assert socket.closed


async def test_complete_end_survives_immediate_socket_disconnect(tunnel):
    connection, socket, _ = tunnel
    stream, body = await start(connection, socket, length=4)
    await socket.incoming.put({"bytes": b"done"})
    await socket.frame("end", stream, bytes_sent=4)
    await socket.incoming.put({"type": "websocket.disconnect"})
    await asyncio.sleep(0)
    assert await collect(body) == b"done"


async def test_send_failure_retires_socket_and_response_future(tunnel):
    connection, socket, registry = tunnel

    async def broken_send(text):
        raise OSError("socket send failed")

    socket.send_text = broken_send
    with pytest.raises(OSError, match="socket send failed"):
        await connection.request("GET", "/file", "", {})
    assert connection.closed
    assert connection.response.cancelled()
    assert registry._idle[("n", "s")].empty()
