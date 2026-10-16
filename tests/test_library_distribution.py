"""Multi-target scheduling, outbound relay and actual WebSocket wakeups."""

import asyncio
import contextlib
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from hololab.gateway.app import create_app
from hololab.gateway.library import CHUNK_SIZE
from hololab.gateway.library_config import LibraryConfig
from hololab.gateway.library_distribution import wake_node
from hololab.library_transport import capability
from hololab.node.fileserver import create_fileserver_app
from hololab.node.library import poll_library
from hololab.protocol import decode, encode
from tests import test_library_transfers as helpers
from tests.test_library_transfers import claim, sha, upload, worker

setup = helpers.setup


async def resource(client, session):
    response = await client.get("/api/library/resources/" + session["resource_id"])
    assert response.status_code == 200, response.text
    return response.json()


async def test_partial_ready_retries_only_failed_target(setup):
    _app, client, root = setup
    session = await upload(client, b"multi-target", targets=["n1", "n2"])
    await worker(root).run_once(client)
    a = (await resource(client, session))["placements"][0]
    (root / "n2").mkdir()
    (root / "n2/movie.mp4").write_bytes(b"conflict")
    await worker(root, "n2").run_once(client)
    state = await resource(client, session)
    assert state["status"] == "partially_ready"
    assert state["placement_summary"] == "1/2"
    b = next(t for t in state["transfers"] if t["target_node_id"] == "n2")
    assert b["state"] == "failed"
    (root / "n2/movie.mp4").unlink()
    assert (
        await client.post(f"/api/library/transfers/{b['transfer_id']}/retry")
    ).status_code == 200
    await worker(root, "n2").run_once(client)
    state = await resource(client, session)
    assert state["status"] == "ready"
    assert state["placement_summary"] == "2/2"
    assert state["placements"][0] == a
    assert next(t for t in state["transfers"] if t["target_node_id"] == "n1")["attempts"] == 1
    assert len((await client.get("/api/library/resources")).json()["resources"]) == 1


class Network(httpx.AsyncBaseTransport):
    def __init__(self, app, root, *, fail_peer=False):
        self.gateway = httpx.ASGITransport(app=app)
        self.peer = httpx.ASGITransport(
            app=create_fileserver_app(
                workspace_root=root / "workspace",
                library_root=root / "n1",
                library_token_provider=lambda: "secret1",
            )
        )
        self.fail_peer = fail_peer
        self.peer_requests = []
        self.uploaded = []

    async def handle_async_request(self, request):
        if "/upload/parts/" in request.url.path:
            self.uploaded.append(request.url.path)
        if request.url.host == "peer":
            assert "authorization" not in request.headers  # Never disclose B's node credential.
            self.peer_requests.append(request.headers["range"])
            if self.fail_peer:
                raise httpx.ConnectError("injected NAT: peer unreachable", request=request)
            return await self.peer.handle_async_request(request)
        return await self.gateway.handle_async_request(request)


async def source_only(app, client, root, data=b"peer-source" * 100):
    session = await upload(client, data)
    await worker(root).run_once(client)
    objects = await app.state.library.rows("SELECT storage_key FROM library_staging_objects")
    for obj in objects:
        (app.state.library.root / obj["storage_key"]).unlink()
    await app.state.library.execute("UPDATE library_staging_objects SET state='evicted'")
    await app.state.library.execute(
        "UPDATE nodes SET advertised_url='http://peer' WHERE node_id='n1'"
    )
    response = await client.post(
        "/api/library/transfers",
        json={"resource_id": session["resource_id"], "target_node_id": "n2"},
    )
    assert response.status_code == 201, response.text
    return session


@pytest.mark.parametrize("fail_peer", [False, True])
async def test_direct_or_outbound_nat_relay(setup, fail_peer):
    app, client, root = setup
    session = await source_only(app, client, root)
    network = Network(app, root, fail_peer=fail_peer)
    async with httpx.AsyncClient(transport=network, base_url="http://gateway") as nodes:
        await worker(root, "n2").run_once(nodes)
        state = await resource(client, session)
        b = next(t for t in state["transfers"] if t["target_node_id"] == "n2")
        if fail_peer:
            assert b["state"] == "waiting_source"
            assert b["route"] == "gateway_relay"
            assert "NAT" in b["fallback_reason"]
            assert state["placement_summary"] == "1/2"
            assert not (root / "n2/movie.mp4").exists()
            assert await worker(root).run_once(nodes)  # A uploads outwards to Gateway.
            assert await worker(root, "n2").run_once(nodes)
            assert len(network.uploaded) == 1
        else:
            assert b["route"] == "peer_direct"
            assert not network.uploaded
        state = await resource(client, session)
        assert state["status"] == "ready"
        assert sha((root / "n2/movie.mp4").read_bytes()) == sha(
            (root / "n1/movie.mp4").read_bytes()
        )
        assert len(await app.state.library.rows("SELECT * FROM library_resources")) == 1
        assert len(network.peer_requests) == 1
        if fail_peer:
            assert all(t["state"] == "completed" for t in state["transfers"])
            assert (
                await app.state.library.rows(
                    "SELECT * FROM library_upload_sessions WHERE purpose='relay'"
                )
            )[0]["state"] == "completed"


async def test_relay_failure_retry_preserves_ready_source(setup):
    app, client, root = setup
    session = await source_only(app, client, root)
    async with httpx.AsyncClient(
        transport=Network(app, root, fail_peer=True), base_url="http://gateway"
    ) as nodes:
        await worker(root, "n2").run_once(nodes)
        original = (root / "n1/movie.mp4").read_bytes()
        (root / "n1/movie.mp4").write_bytes(b"changed")
        await worker(root).run_once(nodes)
        state = await resource(client, session)
        assert state["placement_summary"] == "1/2"
        b = next(t for t in state["transfers"] if t["target_node_id"] == "n2")
        assert b["state"] == "failed"
        (root / "n1/movie.mp4").write_bytes(original)
        response = await client.post(f"/api/library/transfers/{b['transfer_id']}/retry")
        assert response.json()["state"] == "waiting_source"
        await worker(root).run_once(nodes)
        await worker(root, "n2").run_once(nodes)
        assert (await resource(client, session))["status"] == "ready"


async def test_relay_quota_is_readable_and_retry_bounded(setup):
    app, client, root = setup
    session = await source_only(app, client, root)
    app.state.library.config.staging_quota_bytes = 1
    async with httpx.AsyncClient(
        transport=Network(app, root, fail_peer=True), base_url="http://gateway"
    ) as nodes:
        for attempt in range(1, 6):
            before = time.time()
            assert await worker(root, "n2").run_once(nodes)
            b = next(
                t
                for t in (await resource(client, session))["transfers"]
                if t["target_node_id"] == "n2"
            )
            assert b["attempts"] == attempt
            if attempt < 5:
                assert b["state"] == "waiting"
                assert before + 2**attempt <= b["next_retry_ts"] <= time.time() + 2**attempt + 1
                await app.state.library.execute(
                    "UPDATE library_transfers SET next_retry_ts=0 WHERE target_node_id='n2'"
                )
            else:
                assert b["state"] == "failed"
        assert "507" in b["last_error"]
        assert not await worker(root, "n2").run_once(nodes)


@pytest.mark.parametrize("field", ["max_concurrent_transfers", "max_transfers_per_resource"])
async def test_concurrency_limits_are_atomic(setup, field):
    app, client, _root = setup
    setattr(app.state.library.config, field, 1)
    await upload(client, b"data", targets=["n1", "n2"])
    results = await asyncio.gather(claim(client, "n1"), claim(client, "n2"))
    assert sum(t is not None for t in results) == 1
    running = next(t for t in results if t)
    assert await claim(client, running["target_node_id"]) is None


async def test_relay_upload_is_owner_and_lease_scoped(setup):
    app, client, root = setup
    await source_only(app, client, root)
    async with httpx.AsyncClient(
        transport=Network(app, root, fail_peer=True), base_url="http://gateway"
    ) as nodes:
        await worker(root, "n2").run_once(nodes)
    task = await claim(client)
    upload_id = task["source_ref"]
    assert (await client.post(f"/api/library/uploads/{upload_id}/complete")).status_code == 403
    headers = worker(root).headers(task)
    endpoint = f"/api/library/nodes/n1/transfers/{task['transfer_id']}/upload"
    assert (
        await client.get(endpoint, headers={**headers, "Authorization": "Bearer secret2"})
    ).status_code == 401
    assert (
        await client.get(endpoint, headers={**headers, "X-Transfer-Lease": "stale"})
    ).status_code == 409
    assert (await client.get(endpoint, headers=headers)).status_code == 200


async def test_capability_rejects_unauthenticated_and_path_escape(tmp_path):
    app = create_fileserver_app(
        workspace_root=tmp_path / "workspace",
        library_root=tmp_path,
        library_token_provider=lambda: "secret",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://peer"
    ) as client:
        assert (await client.get("/_library/content")).status_code == 403
        for path in ["../outside", ".hololab-tmp/private", "symlink"]:
            if path == "symlink":
                (tmp_path / path).symlink_to("/etc/hosts")
            token = capability("secret", path=path, sha256="a" * 64, size=10)
            response = await client.get(
                "/_library/content", headers={"X-Library-Capability": token, "Range": "bytes=0-1"}
            )
            assert response.status_code == 404


def test_real_websocket_wakeup_and_disconnected_http_claim(tmp_path):
    app = create_app(
        db_path=tmp_path / "db", library_config=LibraryConfig(staging_root=tmp_path / "staging")
    )
    with TestClient(app) as client:
        with client.websocket_connect("/ws/node") as ws:
            ws.send_text(
                encode(
                    "register",
                    {"node_name": "library-ws", "v_min": 1, "v_max": 1, "library_wakeup": True},
                )
            )
            env, registered = decode(ws.receive_text())
            assert env.kind == "register_ok"
            data = b"ws wake"
            response = client.post(
                "/api/library/uploads",
                json={
                    "filename": "ws.bin",
                    "size_bytes": len(data),
                    "sha256": sha(data),
                    "target_node_ids": [registered.node_id],
                },
            )
            assert response.status_code == 201, response.text
            uid = response.json()["upload_id"]
            assert (
                client.put(
                    f"/api/library/uploads/{uid}/parts/0",
                    content=data,
                    headers={"X-Chunk-SHA256": sha(data)},
                ).status_code
                == 200
            )
            assert client.post(f"/api/library/uploads/{uid}/complete").status_code == 200
            env, payload = decode(ws.receive_text())
            assert env.kind == "library_wake"
            assert payload.reason == "pending_transfers"
        # Node identity remains valid after the control connection closes.
        response = client.post(
            f"/api/library/nodes/{registered.node_id}/transfers/claim",
            headers={"Authorization": f"Bearer {registered.node_token}"},
        )
        assert response.status_code == 200
        assert response.json()["transfer"] is not None


async def test_worker_wake_and_poll_fallback_without_ws(monkeypatch, tmp_path):
    from hololab.node import library

    calls = asyncio.Queue()

    async def run_once(self, client):
        calls.put_nowait(time.monotonic())
        return False

    monkeypatch.setattr(library.LibraryWorker, "run_once", run_once)
    monkeypatch.setattr(library, "POLL_SECONDS", 0.15)
    runtime = SimpleNamespace(
        _node_id="n1",
        _node_token="secret1",
        _ws_ready=asyncio.Event(),
        _library_wake=asyncio.Event(),
        _config=SimpleNamespace(library_root=tmp_path, gateway_url="http://gateway"),
    )
    task = asyncio.create_task(poll_library(runtime))
    try:
        await asyncio.wait_for(calls.get(), 2)
        runtime._library_wake.set()
        await asyncio.wait_for(calls.get(), 0.1)  # Wake before polling deadline.
        await asyncio.wait_for(calls.get(), 0.5)  # Timer works with WS unset.
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_old_node_does_not_receive_unknown_envelope(setup):
    app, _, _ = setup
    assert not await wake_node(app, "n1")


async def test_relay_resume_reuses_parts_and_target_range_checkpoint(setup):
    app, client, root = setup
    data = b"a" * CHUNK_SIZE + b"tail"
    session = await source_only(app, client, root, data)

    class InterruptedRelay(Network):
        def __init__(self, app, root):
            super().__init__(app, root)
            self.failed_upload = False
            self.gateway_ranges = []

        async def handle_async_request(self, request):
            if request.url.host == "peer" and request.headers.get("range", "").startswith(
                f"bytes={CHUNK_SIZE}-"
            ):
                raise httpx.ConnectError("Peer lost at second Range", request=request)
            if request.url.host == "gateway" and request.url.path.endswith("/content"):
                self.gateway_ranges.append(request.headers["range"])
            if request.url.path.endswith("/upload/parts/1") and not self.failed_upload:
                self.failed_upload = True
                raise httpx.ConnectError("Source outbound upload interrupted", request=request)
            return await super().handle_async_request(request)

    network = InterruptedRelay(app, root)
    async with httpx.AsyncClient(transport=network, base_url="http://gateway") as nodes:
        await worker(root, "n2").run_once(nodes)
        assert not (root / "n2/movie.mp4").exists()
        await worker(root).run_once(nodes)
        relay = (
            await app.state.library.rows(
                "SELECT * FROM library_transfers WHERE action='relay_upload'"
            )
        )[0]
        assert relay["state"] == "waiting"
        assert relay["confirmed_bytes"] == CHUNK_SIZE
        # Reconcile durable parts exactly as Gateway startup does; replace worker too.
        await app.state.library.reconcile(await app.state.library.session(relay["source_ref"]))
        await app.state.library.execute(
            "UPDATE library_transfers SET next_retry_ts=0 WHERE action='relay_upload'"
        )
        await worker(root).run_once(nodes)
        assert sum(path.endswith("/parts/0") for path in network.uploaded) == 1
        await worker(root, "n2").run_once(nodes)
        assert network.gateway_ranges == [f"bytes={CHUNK_SIZE}-{len(data) - 1}"]
        assert (root / "n2/movie.mp4").read_bytes() == data
        assert (await resource(client, session))["status"] == "ready"


async def test_exhausted_relay_lease_fails_dependents_without_changing_source(setup):
    app, client, root = setup
    session = await source_only(app, client, root)
    async with httpx.AsyncClient(
        transport=Network(app, root, fail_peer=True), base_url="http://gateway"
    ) as nodes:
        await worker(root, "n2").run_once(nodes)
    assert (await claim(client))["action"] == "relay_upload"
    await app.state.library.execute(
        "UPDATE library_transfers SET lease_until=0,attempts=5 WHERE action='relay_upload'"
    )
    assert await claim(client) is None
    state = await resource(client, session)
    assert state["status"] == "partially_ready"
    assert state["placement_summary"] == "1/2"
    assert next(p for p in state["placements"] if p["node_id"] == "n1")["observed_state"] == "ready"
    assert next(t for t in state["transfers"] if t["target_node_id"] == "n2")["state"] == "failed"


async def test_per_node_limit_separate_resources(setup):
    app, client, _root = setup
    await upload(client, b"first", name="one")
    await upload(client, b"second", name="two")
    assert await claim(client)
    assert await claim(client) is None
    app.state.library.config.max_transfers_per_node = 2
    assert await claim(client)
