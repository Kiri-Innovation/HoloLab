"""Node-pulled transfer protocol, leases, checkpoint recovery and publication."""

import asyncio
import hashlib
import json

import httpx
import pytest
import pytest_asyncio

from hololab.gateway.app import create_app
from hololab.gateway.library import CHUNK_SIZE
from hololab.gateway.library_config import LibraryConfig
from hololab.node.library import LibraryWorker, TransferError


def sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest_asyncio.fixture
async def setup(tmp_path):
    app = create_app(
        db_path=tmp_path / "db.sqlite",
        library_config=LibraryConfig(staging_root=tmp_path / "staging"),
    )
    lifecycle = app.router.lifespan_context(app)
    await lifecycle.__aenter__()

    async def seed(conn):
        await conn.executemany(
            "INSERT INTO nodes(node_id,node_name,node_token,created_ts,online) VALUES (?,?,?,0,1)",
            [("n1", "node1", "secret1"), ("n2", "node2", "secret2")],
        )

    await app.state.db.write(seed)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway"
    ) as client:
        yield app, client, tmp_path
    await lifecycle.__aexit__(None, None, None)


async def upload(client, data, *, name="movie.mp4", targets=None):
    response = await client.post(
        "/api/library/uploads",
        json={
            "filename": name,
            "size_bytes": len(data),
            "sha256": sha(data),
            "target_node_ids": ["n1"] if targets is None else targets,
        },
    )
    assert response.status_code == 201, response.text
    session = response.json()
    for index, offset in enumerate(range(0, len(data), CHUNK_SIZE)):
        part = data[offset : offset + CHUNK_SIZE]
        response = await client.put(
            f"/api/library/uploads/{session['upload_id']}/parts/{index}",
            content=part,
            headers={"X-Chunk-SHA256": sha(part)},
        )
        assert response.status_code == 200, response.text
    response = await client.post(f"/api/library/uploads/{session['upload_id']}/complete")
    assert response.status_code == 200, response.text
    return session


def worker(root, node="n1"):
    return LibraryWorker(
        root / node, "ws://gateway", node, "secret1" if node == "n1" else "secret2"
    )


async def claim(client, node="n1"):
    response = await client.post(
        f"/api/library/nodes/{node}/transfers/claim",
        headers={"Authorization": "Bearer secret1" if node == "n1" else "Bearer secret2"},
    )
    assert response.status_code == 200, response.text
    return response.json()["transfer"]


async def state(app):
    return (await app.state.library.rows("SELECT * FROM library_transfers"))[0]


async def test_real_api_materialization_and_ack_retry(setup):
    app, client, root = setup
    data = b"a" * CHUNK_SIZE + b"last"
    await upload(client, data)
    w = worker(root)
    assert await w.run_once(client)
    assert (root / "n1/movie.mp4").read_bytes() == data
    assert w.ranges == [(0, CHUNK_SIZE - 1), (CHUNK_SIZE, len(data) - 1)]
    transfer = await state(app)
    assert transfer["state"] == "completed"
    placement = (await app.state.library.rows("SELECT * FROM library_placements"))[0]
    assert placement["observed_state"] == "ready"
    assert placement["verified_sha256"] == sha(data)
    headers = {**w.auth, "X-Transfer-Lease": transfer["lease_token"]}
    response = await client.post(
        w.prefix + f"/{transfer['transfer_id']}/receipt",
        headers=headers,
        json={"status": "success", "confirmed_bytes": len(data), "sha256": sha(data)},
    )
    assert response.status_code == 200
    assert not await w.run_once(client)


async def test_manual_transfer_and_independent_node_placement(setup):
    _app, client, root = setup
    session = await upload(client, b"data", targets=[])
    body = {"resource_id": session["resource_id"], "target_node_id": "n2"}
    first = await client.post("/api/library/transfers", json=body)
    assert first.status_code == 201
    assert (await client.post("/api/library/transfers", json=body)).json()[
        "transfer_id"
    ] == first.json()["transfer_id"]
    assert await claim(client, "n1") is None
    assert await worker(root, "n2").run_once(client)
    assert (root / "n2/movie.mp4").read_bytes() == b"data"


async def test_node_auth_lease_fencing_and_range_validation(setup):
    app, client, root = setup
    await upload(client, b"abc")
    assert (await client.post("/api/library/nodes/n1/transfers/claim")).status_code == 401
    task = await claim(client)
    w = worker(root)
    endpoint = w.prefix + f"/{task['transfer_id']}"
    assert (
        await client.get(endpoint + "/content", headers={**w.headers(task), "Range": "bytes=0-2"})
    ).content == b"abc"
    for value in ["bytes=2-1", "bytes=0-3", "bytes=-1", "bytes=0-1,2-2", ""]:
        response = await client.get(
            endpoint + "/content", headers={**w.headers(task), "Range": value}
        )
        assert response.status_code == 416
    assert (
        await client.get(
            endpoint + "/content",
            headers={**w.headers(task), "Range": "bytes=0-2", "If-Range": "bad"},
        )
    ).status_code == 412
    assert (
        await client.post(
            endpoint + "/receipt",
            headers=w.headers(task),
            json={"status": "success", "confirmed_bytes": 3, "sha256": "0" * 64},
        )
    ).status_code == 422
    await app.state.library.execute("UPDATE library_transfers SET lease_until=0")
    new = await claim(client)
    assert new["lease_token"] != task["lease_token"]
    assert (
        await client.post(
            endpoint + "/receipt",
            headers=w.headers(task),
            json={"status": "success", "confirmed_bytes": 3, "sha256": sha(b"abc")},
        )
    ).status_code == 409
    assert (
        await client.get(
            "/api/library/nodes/n2/transfers/" + task["transfer_id"] + "/content",
            headers={
                "Authorization": "Bearer secret2",
                "X-Transfer-Lease": new["lease_token"],
                "Range": "bytes=0-2",
            },
        )
    ).status_code == 409


class InterruptTransport(httpx.AsyncBaseTransport):
    def __init__(self, app, *, fail_at=CHUNK_SIZE, mutate=None):
        self.inner = httpx.ASGITransport(app=app)
        self.fail_at = fail_at
        self.mutate = mutate
        self.gets = []

    async def handle_async_request(self, request):
        if request.url.path.endswith("/content"):
            self.gets.append(request.headers["range"])
            if request.headers["range"].startswith(f"bytes={self.fail_at}-"):
                raise httpx.ReadError("simulated disconnection", request=request)
            response = await self.inner.handle_async_request(request)
            if self.mutate:
                return await self.mutate(response)
            return response
        return await self.inner.handle_async_request(request)


async def test_interruption_restart_and_no_visible_partial(setup):
    app, client, root = setup
    data = b"a" * CHUNK_SIZE + b"b" * CHUNK_SIZE + b"tail"
    await upload(client, data)
    task = await claim(client)
    w = worker(root)
    transport = InterruptTransport(app)
    async with httpx.AsyncClient(transport=transport) as interrupted:
        with pytest.raises(httpx.ReadError):
            await w.materialize(interrupted, task)
    assert not (root / "n1/movie.mp4").exists()
    journal = root / "n1/.hololab-tmp" / task["transfer_id"] / "state.json"
    assert json.loads(journal.read_text())["confirmed_bytes"] == CHUNK_SIZE
    new_worker = worker(root)
    result = await new_worker.materialize(client, task)
    assert result["sha256"] == sha(data)
    assert new_worker.ranges[0][0] == CHUNK_SIZE
    assert new_worker.received_bytes == len(data) - CHUNK_SIZE
    assert (root / "n1/movie.mp4").read_bytes() == data


async def test_wrong_sha_never_publishes_and_retry_bounded(setup):
    app, client, root = setup
    session = await upload(client, b"abc")
    payload = root / "staging" / session["upload_id"] / "payload"
    payload.write_bytes(b"bad")  # same-length corruption in source
    w = worker(root)
    await w.run_once(client)
    first = await state(app)
    assert first["state"] == "waiting"
    assert "checksum_mismatch" in first["last_error"]
    assert not (root / "n1/movie.mp4").exists()
    await app.state.library.execute("UPDATE library_transfers SET next_retry_ts=0")
    await w.run_once(client)
    assert (await state(app))["state"] == "failed"
    assert not (root / "n1/movie.mp4").exists()
    payload.write_bytes(b"abc")
    retry = await client.post(f"/api/library/transfers/{first['transfer_id']}/retry")
    assert retry.status_code == 200
    await w.run_once(client)
    assert (await state(app))["state"] == "completed"


async def test_existing_file_and_symlink_parent_are_not_overwritten(setup):
    app, client, root = setup
    await upload(client, b"abc")
    (root / "n1").mkdir()
    (root / "n1/movie.mp4").write_bytes(b"keep")
    await worker(root).run_once(client)
    assert (root / "n1/movie.mp4").read_bytes() == b"keep"
    assert (await state(app))["state"] == "failed"
    task = await claim(client)
    assert task is None
    for name in ["../escape", "/absolute", ".hololab-tmp/hack", "a/../b", "a\\b"]:
        with pytest.raises(TransferError):
            await worker(root).materialize(client, {"relative_path": name})
    # Registered directory path can never follow a symlink outside the root.
    folder = (await client.post("/api/library/folders", json={"name": "linked"})).json()[
        "folder_id"
    ]
    created = await client.post(
        "/api/library/uploads",
        json={
            "filename": "data",
            "size_bytes": 0,
            "sha256": sha(b""),
            "folder_id": folder,
            "target_node_ids": ["n1"],
        },
    )
    await client.post(f"/api/library/uploads/{created.json()['upload_id']}/complete")
    (root / "outside").mkdir()
    (root / "n1/linked").symlink_to(root / "outside", target_is_directory=True)
    await worker(root).run_once(client)
    assert list((root / "outside").iterdir()) == []


async def test_atomic_publication_crash_and_lost_receipt(setup, monkeypatch):
    import hololab.node.library as node_library

    app, client, root = setup
    await upload(client, b"abc")
    task = await claim(client)
    original = node_library.write_journal

    def crash_after_link(fd, journal):
        if journal["state"] == "ready":
            raise OSError("simulated crash after publication")
        original(fd, journal)

    with monkeypatch.context() as patch:
        patch.setattr(node_library, "write_journal", crash_after_link)
        with pytest.raises(OSError):
            await worker(root).materialize(client, task)
    assert (root / "n1/movie.mp4").read_bytes() == b"abc"
    # Reclaim after the old worker dies; no network payload is needed again.
    await app.state.library.execute("UPDATE library_transfers SET lease_until=0")
    new = worker(root)
    await new.run_once(client)
    assert new.received_bytes == 0
    assert (await state(app))["state"] == "completed"


@pytest.mark.parametrize("mode", ["full_200", "wrong_range", "short_body"])
async def test_invalid_server_responses_never_append_or_publish(setup, mode):
    app, client, root = setup
    await upload(client, b"abc")
    task = await claim(client)

    async def mutate(response):
        body = await response.aread()
        headers = dict(response.headers)
        if mode == "wrong_range":
            headers["content-range"] = "bytes 1-3/3"
        if mode == "short_body":
            body = body[:1]

        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield body

        return httpx.Response(200 if mode == "full_200" else 206, headers=headers, stream=Body())

    async with httpx.AsyncClient(
        transport=InterruptTransport(app, fail_at=-1, mutate=mutate)
    ) as bad:
        with pytest.raises(TransferError):
            await worker(root).materialize(bad, task)
    assert not (root / "n1/movie.mp4").exists()
    partial = root / "n1/.hololab-tmp" / task["transfer_id"] / "payload.part"
    assert partial.stat().st_size == 0


async def test_concurrent_claim_and_expired_attempt_limit(setup):
    app, client, _root = setup
    await upload(client, b"abc")
    results = await asyncio.gather(claim(client), claim(client))
    assert sum(r is not None for r in results) == 1
    await app.state.library.execute("UPDATE library_transfers SET attempts=5,lease_until=0")
    assert await claim(client) is None
    assert (await state(app))["state"] == "failed"


async def test_empty_file_and_upgrade_backfill(setup):
    app, client, root = setup
    await upload(client, b"")
    # Simulate a completed V15 upload with a pending placement and no task.
    await app.state.library.execute("DELETE FROM library_transfers")
    await app.state.library.startup()
    w = worker(root)
    await w.run_once(client)
    assert w.ranges == []
    assert (root / "n1/movie.mp4").stat().st_size == 0
    assert (await state(app))["state"] == "completed"


async def test_lost_lease_before_publication(setup, monkeypatch):
    app, client, root = setup
    await upload(client, b"abc")
    task = await claim(client)
    w = worker(root)
    original = w.renew
    calls = 0

    async def expire_before_publish(client, task):
        nonlocal calls
        calls += 1
        if calls == 2:
            await app.state.library.execute("UPDATE library_transfers SET lease_until=0")
        await original(client, task)

    monkeypatch.setattr(w, "renew", expire_before_publish)
    with pytest.raises(httpx.HTTPStatusError):
        await w.materialize(client, task)
    assert not (root / "n1/movie.mp4").exists()
    assert (await state(app))["state"] == "running"
