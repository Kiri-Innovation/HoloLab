"""Deletion fencing, offline acknowledgements, refusal rules and staging quota GC."""

import asyncio
import contextlib
import fcntl
import os
import time

import pytest
import pytest_asyncio

from hololab.gateway.library_gc import collect
from tests import test_library_transfers as helpers
from tests.test_library_transfers import claim, sha, upload, worker

setup = helpers.setup


@pytest_asyncio.fixture
async def isolated(setup):
    app, client, root = setup
    app.state.library_gc_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await app.state.library_gc_task
    return app, client, root


async def get(client, rid):
    return await client.get("/api/library/resources/" + rid)


async def delete(client, rid):
    response = await client.delete("/api/library/resources/" + rid)
    assert response.status_code == 202, response.text
    return response.json()


async def test_two_phase_offline_node_and_name_reservation(isolated):
    app, client, root = isolated
    session = await upload(client, b"content", targets=["n1", "n2"])
    rid = session["resource_id"]
    await worker(root).run_once(client)
    await worker(root, "n2").run_once(client)
    await app.state.library.execute("UPDATE nodes SET online=0 WHERE node_id='n2'")
    first = await delete(client, rid)
    assert first["delete_epoch"] == 1
    assert first["status"] == "deleting" and first["remaining_nodes"] == 2
    assert await claim(client) is None  # Old nodes cannot receive delete as a download.
    await worker(root).run_once(client)
    await collect(app.state.library)
    state = (await get(client, rid)).json()
    assert state["remaining_nodes"] == 1
    assert not (root / "n1/movie.mp4").exists()
    assert (root / "n2/movie.mp4").exists()
    assert (await client.get("/api/library/resources")).json()["total"] == 1
    response = await client.post(
        "/api/library/uploads",
        json={"filename": "movie.mp4", "size_bytes": 1, "sha256": sha(b"x"), "target_node_ids": []},
    )
    assert response.status_code == 409
    assert (await delete(client, rid))["delete_epoch"] == 1
    await app.state.library.execute("UPDATE nodes SET online=1 WHERE node_id='n2'")
    await worker(root, "n2").run_once(client)
    await collect(app.state.library)
    assert (await get(client, rid)).status_code == 404
    assert not (root / "n2/movie.mp4").exists()
    for table in (
        "library_resources",
        "library_resource_versions",
        "library_placements",
        "library_transfers",
        "library_upload_sessions",
        "library_upload_parts",
        "library_staging_objects",
    ):
        assert not await app.state.library.rows(f"SELECT * FROM {table}")
    await upload(client, b"new file")  # Name released only after all acknowledgements.


async def test_staging_only_delete_and_unknown_resource(isolated):
    app, client, _root = isolated
    session = await upload(client, b"only copy", targets=[])
    assert (await delete(client, session["resource_id"]))["remaining_nodes"] == 0
    await collect(app.state.library)
    assert not (app.state.library.root / session["upload_id"]).exists()
    assert (await get(client, session["resource_id"])).status_code == 404
    assert (await client.delete("/api/library/resources/unknown")).status_code == 404


@pytest.mark.parametrize("change", ["content", "symlink", "directory", "unowned"])
async def test_modified_or_unowned_files_refused(isolated, change):
    app, client, root = isolated
    session = await upload(client, b"original")
    await worker(root).run_once(client)
    path = root / "n1/movie.mp4"
    if change == "content":
        path.write_bytes(b"modified")
    elif change == "symlink":
        path.unlink()
        path.symlink_to("/etc/hosts")
    elif change == "directory":
        path.unlink()
        path.mkdir()
    else:
        for journal in (root / "n1/.hololab-tmp").glob("*/state.json"):
            journal.unlink()
    await delete(client, session["resource_id"])
    await worker(root).run_once(client)
    await collect(app.state.library)
    state = (await get(client, session["resource_id"])).json()
    assert state["status"] == "deleting" and state["remaining_nodes"] == 1
    assert any(t["state"] == "failed" and "refusing" in t["last_error"] for t in state["transfers"])
    assert path.exists()
    if change == "content":
        path.write_bytes(b"original")
        await delete(client, session["resource_id"])
        await worker(root).run_once(client)
        await collect(app.state.library)
        assert (await get(client, session["resource_id"])).status_code == 404


async def test_active_download_is_fenced_and_busy_cleanup_waits(isolated):
    app, client, root = isolated
    session = await upload(client, b"data")
    task = await claim(client)
    w = worker(root)
    # Real materialization journal and writer lock, before any receipt.
    await w.materialize(client, task)
    directory = root / "n1/.hololab-tmp" / task["transfer_id"]
    fd = os.open(directory / "owner.lock", os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        await delete(client, session["resource_id"])
        response = await client.post(
            w.prefix + f"/{task['transfer_id']}/receipt",
            headers=w.headers(task),
            json={"status": "success", "confirmed_bytes": 4, "sha256": sha(b"data")},
        )
        assert response.status_code == 409
        await w.run_once(client)
        state = (await get(client, session["resource_id"])).json()
        assert any(t["action"] == "delete" and t["state"] == "waiting" for t in state["transfers"])
        assert (root / "n1/movie.mp4").exists()
    finally:
        os.close(fd)
    await app.state.library.execute(
        "UPDATE library_transfers SET next_retry_ts=0 WHERE action='delete'"
    )
    await w.run_once(client)
    await collect(app.state.library)
    assert (await get(client, session["resource_id"])).status_code == 404


async def test_abandoned_upload_ttl_releases_quota(isolated):
    app, client, _root = isolated
    data = b"partial"
    response = await client.post(
        "/api/library/uploads",
        json={
            "filename": "abandoned.bin",
            "size_bytes": len(data),
            "sha256": sha(data),
            "target_node_ids": [],
        },
    )
    session = response.json()
    await client.put(
        f"/api/library/uploads/{session['upload_id']}/parts/0",
        content=data,
        headers={"X-Chunk-SHA256": sha(data)},
    )
    await collect(app.state.library)
    assert (await app.state.library.session(session["upload_id"]))["reserved_bytes"] > 0
    await app.state.library.execute(
        "UPDATE library_upload_sessions SET last_activity_ts=?", (time.time() - 86401,)
    )
    await collect(app.state.library)
    state = await app.state.library.session(session["upload_id"])
    assert state["state"] == "cancelled" and state["reserved_bytes"] == 0
    assert not (app.state.library.root / session["upload_id"]).exists()
    assert not (await client.get("/api/library/resources")).json()["resources"]


async def test_completed_staging_retained_until_all_ready_and_restart_does_not_reserve_again(
    isolated,
):
    app, client, root = isolated
    session = await upload(client, b"data", targets=["n1", "n2"])
    payload = app.state.library.root / session["upload_id"] / "payload"
    await worker(root).run_once(client)
    await collect(app.state.library)
    assert payload.exists()
    await worker(root, "n2").run_once(client)
    # Simulate a crash after excluding the object from reads, before unlinking.
    await app.state.library.execute("UPDATE library_staging_objects SET state='purging'")
    await app.state.library.startup()
    assert (await app.state.library.session(session["upload_id"]))["reserved_bytes"] == 4
    await collect(app.state.library)
    assert not payload.exists()
    assert (await app.state.library.session(session["upload_id"]))["reserved_bytes"] == 0
    await app.state.library.startup()
    assert (await app.state.library.session(session["upload_id"]))["reserved_bytes"] == 0
    only = await upload(client, b"only", name="only.bin", targets=[])
    await collect(app.state.library)
    assert (app.state.library.root / only["upload_id"] / "payload").exists()


async def test_cleanup_stale_epoch_auth_and_exhausted_lease(isolated):
    app, client, _root = isolated
    session = await upload(client, b"data")
    await delete(client, session["resource_id"])
    prefix = "/api/library/nodes/n1/deletions"
    assert (await client.post(prefix + "/claim")).status_code == 401
    task = (
        await client.post(prefix + "/claim", headers={"Authorization": "Bearer secret1"})
    ).json()["transfer"]
    endpoint = prefix + f"/{task['transfer_id']}/receipt"
    body = {"status": "success", "confirmed_bytes": 0}
    assert (
        await client.post(
            endpoint,
            headers={"Authorization": "Bearer secret1", "X-Transfer-Lease": "stale"},
            json=body,
        )
    ).status_code == 409
    await app.state.library.execute(
        "UPDATE library_transfers SET lease_until=0,attempts=5 WHERE action='delete'"
    )
    assert (
        await client.post(prefix + "/claim", headers={"Authorization": "Bearer secret1"})
    ).json()["transfer"] is None
    await collect(app.state.library)
    assert (await get(client, session["resource_id"])).json()["remaining_nodes"] == 1


async def test_pagination_and_concurrent_name_conflict(isolated):
    _app, client, _root = isolated
    body = {"filename": "same.bin", "size_bytes": 1, "sha256": sha(b"x"), "target_node_ids": []}
    responses = await asyncio.gather(
        client.post("/api/library/uploads", json=body),
        client.post("/api/library/uploads", json=body),
    )
    assert sorted(r.status_code for r in responses) == [201, 409]
    await upload(client, b"a", name="a.bin", targets=[])
    page = (await client.get("/api/library/resources?folder_id=root&limit=1&offset=1")).json()
    assert page["total"] == 2 and len(page["resources"]) == 1
    assert (await client.get("/api/library/resources?limit=0")).status_code == 422
    await client.post("/api/library/folders", json={"name": "one"})
    assert len((await client.get("/api/library/folders?limit=1&offset=1")).json()["folders"]) == 1


async def test_staging_cleanup_error_blocks_finalization_and_recovers(isolated, monkeypatch):
    from hololab.gateway import library_gc

    app, client, _root = isolated
    session = await upload(client, b"staged", targets=[])
    await delete(client, session["resource_id"])
    original = library_gc.shutil.rmtree

    def refuse(path):
        raise PermissionError("injected staging permission failure")

    monkeypatch.setattr(library_gc.shutil, "rmtree", refuse)
    await collect(app.state.library)
    result = (await get(client, session["resource_id"])).json()
    assert result["status"] == "deleting"
    assert "permission failure" in result["gc_errors"][0]["last_error"]
    monkeypatch.setattr(library_gc.shutil, "rmtree", original)
    await collect(app.state.library)
    assert (await get(client, session["resource_id"])).status_code == 404


async def test_upload_cancel_cannot_hide_deleting_resource(isolated):
    app, client, _root = isolated
    response = await client.post(
        "/api/library/uploads",
        json={
            "filename": "pending",
            "size_bytes": 1,
            "sha256": sha(b"x"),
            "target_node_ids": ["n1"],
        },
    )
    session = response.json()
    await delete(client, session["resource_id"])
    assert (await client.delete("/api/library/uploads/" + session["upload_id"])).status_code == 409
    assert (await get(client, session["resource_id"])).json()["status"] == "deleting"
    await collect(app.state.library)
    assert (await get(client, session["resource_id"])).status_code == 200


async def test_abandoned_failed_relay_frees_quota_and_can_retry(isolated):
    import httpx

    from tests.test_library_distribution import Network, source_only

    app, client, root = isolated
    session = await source_only(app, client, root)
    async with httpx.AsyncClient(
        transport=Network(app, root, fail_peer=True), base_url="http://gateway"
    ) as nodes:
        await worker(root, "n2").run_once(nodes)
        original = (root / "n1/movie.mp4").read_bytes()
        (root / "n1/movie.mp4").write_bytes(b"changed")
        await worker(root).run_once(nodes)
        relay = (
            await app.state.library.rows(
                "SELECT * FROM library_upload_sessions WHERE purpose='relay'"
            )
        )[0]
        await app.state.library.execute(
            "UPDATE library_upload_sessions SET last_activity_ts=? WHERE purpose='relay'",
            (time.time() - 86401,),
        )
        await collect(app.state.library)
        expired = await app.state.library.session(relay["upload_id"])
        assert expired["reserved_bytes"] == 0 and expired["state"] == "cancelled"
        assert (await get(client, session["resource_id"])).json()["lifecycle"] == "active"
        (root / "n1/movie.mp4").write_bytes(original)
        b = next(
            t
            for t in (await get(client, session["resource_id"])).json()["transfers"]
            if t["target_node_id"] == "n2"
        )
        assert (
            await client.post("/api/library/transfers/" + b["transfer_id"] + "/retry")
        ).status_code == 200
        assert (await app.state.library.session(relay["upload_id"]))["reserved_bytes"] == len(
            original
        ) * 2
        await worker(root).run_once(nodes)
        await worker(root, "n2").run_once(nodes)
        assert (await get(client, session["resource_id"])).json()["status"] == "ready"
