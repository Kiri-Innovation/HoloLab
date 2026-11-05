"""Exercise actual Uvicorn framing without opening ports or spawning services."""

import asyncio
import logging
from unittest.mock import Mock

import pytest
from uvicorn.protocols.http.flow_control import FlowControl
from uvicorn.protocols.http.httptools_impl import RequestResponseCycle

from hololab.node.fileserver import create_fileserver_app


async def _request(
    app, path, *, headers=(), mutate=None, after_body=None, query=b"", extensions=None
):
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query,
        "root_path": "",
        "headers": list(headers),
        "extensions": extensions or {},
        "server": ("localhost", 80),
        "client": ("localhost", 12345),
    }
    transport = Mock()
    logger = logging.getLogger(__name__)
    cycle = RequestResponseCycle(
        scope=scope,
        transport=transport,
        flow=FlowControl(transport),
        logger=logger,
        access_logger=logger,
        access_log=False,
        default_headers=[],
        message_event=asyncio.Event(),
        expect_100_continue=False,
        keep_alive=True,
        on_response=lambda: None,
    )
    messages = []

    async def send(message):
        await cycle.send(message)  # Raises the production framing error on the old code.
        messages.append(message)
        if message["type"] == "http.response.start" and mutate:
            mutate()
        if message["type"] == "http.response.body" and message.get("body") and after_body:
            after_body()

    async def receive():
        await asyncio.Event().wait()

    await app(scope, receive, send)
    assert cycle.response_complete
    response_headers = dict(messages[0]["headers"])
    body = b"".join(m.get("body", b"") for m in messages)
    assert len(body) == int(response_headers[b"content-length"])
    return messages[0]["status"], response_headers, body


@pytest.mark.parametrize("size", [0, 3, 65536, 65539, 200000])
@pytest.mark.parametrize("mutation", ["append", "replace"])
async def test_file_mutation_after_headers(tmp_path, size, mutation):
    path = tmp_path / "artifact.png"
    original = b"x" * size
    path.write_bytes(original)

    def mutate():
        if mutation == "append":
            with path.open("ab") as writer:
                writer.write(b"growth" * 20000)
        else:
            replacement = tmp_path / "replacement.png"
            replacement.write_bytes(b"replacement" * 20000)
            replacement.replace(path)

    status, _, body = await _request(
        create_fileserver_app(workspace_root=tmp_path),
        "/artifact.png",
        mutate=mutate,
    )
    assert status == 200
    assert body == original


@pytest.mark.parametrize("range_header", [b"bytes=2-5", b"bytes=-4", b"bytes=3-", b"bytes=0-1,8-9"])
async def test_range_uses_open_file_after_replacement(tmp_path, range_header):
    path = tmp_path / "artifact.bin"
    original = b"0123456789"
    path.write_bytes(original)

    def mutate():
        replacement = tmp_path / "new.bin"
        replacement.write_bytes(b"z" * 20)
        replacement.replace(path)

    status, headers, body = await _request(
        create_fileserver_app(workspace_root=tmp_path),
        "/artifact.bin",
        headers=[(b"range", range_header)],
        mutate=mutate,
    )
    assert status == 206
    if b"," in range_header:
        assert b"\r\n01\r\n" in body and b"\r\n89\r\n" in body
    else:
        start, end = map(int, headers[b"content-range"].split()[1].split(b"/")[0].split(b"-"))
        assert body == original[start : end + 1]


async def test_growth_during_large_file_stream(tmp_path):
    path = tmp_path / "large.bin"
    original = b"x" * (65536 * 3 + 17)
    path.write_bytes(original)

    def append():
        with path.open("ab") as writer:
            writer.write(b"later" * 100)

    _, _, body = await _request(
        create_fileserver_app(workspace_root=tmp_path),
        "/large.bin",
        after_body=append,
        extensions={"http.response.pathsend": {}},
    )
    assert body == original


async def test_truncation_is_not_silently_successful(tmp_path):
    path = tmp_path / "artifact.bin"
    path.write_bytes(b"original")
    with pytest.raises(RuntimeError, match="shorter than expected"):
        await _request(
            create_fileserver_app(workspace_root=tmp_path),
            "/artifact.bin",
            mutate=lambda: path.write_bytes(b""),
        )


@pytest.mark.parametrize("spool_limit", [1, 32 * 1024 * 1024])
async def test_tar_actual_size_includes_headers_and_padding(tmp_path, monkeypatch, spool_limit):
    from hololab.node import fileserver

    monkeypatch.setattr(fileserver, "_INMEM_TAR_LIMIT_BYTES", spool_limit)
    directory = tmp_path / "directory"
    directory.mkdir()
    (directory / "tiny.txt").write_bytes(b"abc")
    status, _, body = await _request(
        create_fileserver_app(workspace_root=tmp_path),
        "/directory",
        query=b"archive=tar",
    )
    assert status == 200
    assert len(body) > 3


async def test_descriptor_closed_when_send_fails(tmp_path, monkeypatch):
    from hololab.node import fileserver

    path = tmp_path / "artifact.bin"
    path.write_bytes(b"original")
    opened = []
    real_open = fileserver.anyio.open_file

    async def tracked_open(*args, **kwargs):
        file = await real_open(*args, **kwargs)
        opened.append(file)
        return file

    monkeypatch.setattr(fileserver.anyio, "open_file", tracked_open)

    def disconnect():
        raise OSError("test client disconnected")

    with pytest.raises(OSError, match="test client disconnected"):
        await _request(
            create_fileserver_app(workspace_root=tmp_path),
            "/artifact.bin",
            mutate=disconnect,
        )
    assert opened and all(file.wrapped.closed for file in opened)
