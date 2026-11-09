from hololab.node.file_tunnel import _terminal_tunnel_auth_status


def test_terminal_tunnel_auth_statuses_do_not_retry() -> None:
    assert _terminal_tunnel_auth_status(401)
    assert _terminal_tunnel_auth_status(403)
    assert not _terminal_tunnel_auth_status(429)
    assert not _terminal_tunnel_auth_status(503)


async def test_node_sends_end_only_after_full_upstream_body():
    import asyncio
    import json

    import httpx

    from hololab.node.file_tunnel import _BODY_CHUNK_SIZE, FileTunnelClient

    data = b"x" * (_BODY_CHUNK_SIZE * 3 + 7)

    async def parts():
        for offset in range(0, len(data), 8191):
            yield data[offset : offset + 8191]

    async def upstream(request):
        return httpx.Response(200, headers={"content-length": str(len(data))}, content=parts())

    class Socket:
        def __init__(self):
            self.sent = []

        async def send(self, message):
            self.sent.append(message)

        async def recv(self):
            await asyncio.Event().wait()

    socket = Socket()
    node = FileTunnelClient(gateway_url="ws://test", file_server_port=1)
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        node._client = client
        await node._serve(socket, {"stream_id": "one", "method": "GET", "path": "/file"})
    assert b"".join(m for m in socket.sent if isinstance(m, bytes)) == data
    assert json.loads(socket.sent[-1]) == {
        "kind": "end",
        "stream_id": "one",
        "bytes_sent": len(data),
    }


async def test_node_detects_short_upstream_even_if_transport_does_not():
    import asyncio
    import json

    import httpx

    from hololab.node.file_tunnel import FileTunnelClient

    async def parts():
        yield b"short"

    async def upstream(request):
        return httpx.Response(200, headers={"content-length": "10"}, content=parts())

    class Socket:
        def __init__(self):
            self.sent = []

        async def send(self, message):
            self.sent.append(message)

        async def recv(self):
            await asyncio.Event().wait()

    socket = Socket()
    node = FileTunnelClient(gateway_url="ws://test", file_server_port=1)
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        node._client = client
        await node._serve(socket, {"stream_id": "one", "method": "GET", "path": "/file"})
    terminal = json.loads(socket.sent[-1])
    assert terminal["kind"] == "error"
    assert "content-length mismatch" in terminal["detail"]
    assert all(not isinstance(m, str) or json.loads(m)["kind"] != "end" for m in socket.sent)


async def test_reader_failure_cancels_and_joins_upstream_pump():
    import asyncio
    import json

    import httpx

    from hololab.node.file_tunnel import FileTunnelClient

    started, stopped = asyncio.Event(), asyncio.Event()

    async def parts():
        try:
            started.set()
            await asyncio.Event().wait()
            yield b"unreachable"
        finally:
            stopped.set()

    async def upstream(request):
        return httpx.Response(200, content=parts())

    class Socket:
        def __init__(self):
            self.sent = []

        async def send(self, message):
            self.sent.append(message)

        async def recv(self):
            await started.wait()
            raise RuntimeError("reader disconnected")

    socket = Socket()
    node = FileTunnelClient(gateway_url="ws://test", file_server_port=1)
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        node._client = client
        await node._serve(socket, {"stream_id": "one", "method": "GET", "path": "/file"})
    assert stopped.is_set()
    assert json.loads(socket.sent[-1])["kind"] == "error"
