"""Real-filesystem namespace operations and bounded browser download protocols."""

import asyncio
import contextlib
import json

import pytest_asyncio

from hololab.gateway.library_gc import collect
from hololab.node.library_management import apply_operation
from tests.test_library_transfers import setup as setup
from tests.test_library_transfers import sha, upload, worker


@pytest_asyncio.fixture
async def isolated(setup):
    app, client, root = setup
    app.state.library_gc_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await app.state.library_gc_task
    yield app, client, root


async def folder(client, name="Original", parent="root"):
    r = await client.post("/api/library/folders", json={"name": name, "parent_id": parent})
    assert r.status_code == 201, r.text
    return r.json()["folder_id"]


async def finish(client, root, op):
    for node in ("n1", "n2"):
        await worker(root, node).run_once(client)
    result = (await client.get("/api/library/operations/" + op["operation_id"])).json()
    assert result["state"] == "completed", result


async def ingest(client, fid, data=b"video bytes", name="clip.mp4", targets=None):
    r = await client.post(
        "/api/library/uploads",
        json={
            "filename": name,
            "folder_id": fid,
            "size_bytes": len(data),
            "sha256": sha(data),
            "target_node_ids": ["n1"] if targets is None else targets,
        },
    )
    assert r.status_code == 201, r.text
    s = r.json()
    uid = s["upload_id"]
    assert (
        await client.put(
            f"/api/library/uploads/{uid}/parts/0",
            content=data,
            headers={"X-Chunk-Sha256": sha(data)},
        )
    ).status_code == 200
    assert (await client.post(f"/api/library/uploads/{uid}/complete")).status_code == 200
    return s


async def test_rename_nonempty_folder_and_resource_then_gc(isolated):
    app, c, root = isolated
    fid = await folder(c)
    s = await ingest(c, fid, targets=["n1", "n2"])
    await worker(root).run_once(c)
    await worker(root, "n2").run_once(c)
    r = await c.post(f"/api/library/folders/{fid}/rename", json={"name": "Renamed"})
    assert r.status_code == 202, r.text
    op = r.json()
    assert (await c.post("/api/library/folders", json={"name": "blocked"})).status_code == 409
    await worker(root).run_once(c)
    assert (root / "n1/Renamed/clip.mp4").read_bytes() == b"video bytes"
    assert (await c.get("/api/library/operations/" + op["operation_id"])).json()[
        "state"
    ] == "pending"
    await worker(root, "n2").run_once(c)
    assert (await c.get("/api/library/resources/" + s["resource_id"])).json()["placements"][0][
        "relative_path"
    ] == "Renamed/clip.mp4"
    r = await c.post(f"/api/library/resources/{s['resource_id']}/rename", json={"name": "new.mp4"})
    assert r.status_code == 202, r.text
    await finish(c, root, r.json())
    assert (root / "n1/Renamed/new.mp4").exists()
    assert (await c.delete("/api/library/resources/" + s["resource_id"])).status_code == 202
    await worker(root).run_once(c)
    await worker(root, "n2").run_once(c)
    await collect(app.state.library)
    assert not (root / "n1/Renamed/new.mp4").exists()  # renamed ownership journal accepted by GC
    r = await c.delete("/api/library/folders/" + fid)
    assert r.status_code == 202, r.text
    await finish(c, root, r.json())
    assert not (root / "n1/Renamed").exists()


async def test_empty_delete_nonempty_and_name_refusals(isolated):
    _, c, root = isolated
    fid = await folder(c)
    await folder(c, "Sibling")
    assert (
        await c.post(f"/api/library/folders/{fid}/rename", json={"name": "Sibling"})
    ).status_code == 409
    assert (
        await c.post(f"/api/library/folders/{fid}/rename", json={"name": "../escape"})
    ).status_code == 422
    assert (await c.delete("/api/library/folders/root")).status_code == 409
    child = await folder(c, "Child", fid)
    r = await c.delete("/api/library/folders/" + fid)
    assert r.status_code == 409 and "not empty" in r.text
    r = await c.delete("/api/library/folders/" + child)
    await finish(c, root, r.json())
    await ingest(c, fid)
    await worker(root).run_once(c)
    r = await c.delete("/api/library/folders/" + fid)
    assert r.status_code == 409 and "not empty" in r.text


async def test_failed_node_retry_and_duplicate_receipt(isolated):
    app, c, root = isolated
    fid = await folder(c)
    # The directory belonged to n1 before its last resource was collected.
    await app.state.library.execute("INSERT INTO library_folder_nodes VALUES (?,?)", (fid, "n1"))
    (root / "n1/Original").mkdir(parents=True)
    (root / "n1/Original/external").write_text("keep")
    op = (await c.delete("/api/library/folders/" + fid)).json()
    await worker(root).run_once(c)
    await worker(root, "n2").run_once(c)
    result = (await c.get("/api/library/operations/" + op["operation_id"])).json()
    assert result["state"] == "pending" and any(n["last_error"] for n in result["nodes"])
    assert (root / "n1/Original/external").exists()
    (root / "n1/Original/external").unlink()
    await app.state.library.execute("UPDATE library_operation_nodes SET next_retry_ts=0")
    await worker(root).run_once(c)
    assert (await c.get("/api/library/operations/" + op["operation_id"])).json()[
        "state"
    ] == "completed"
    response = await c.post(
        f"/api/library/nodes/n1/operations/{op['operation_id']}/receipt",
        headers={"Authorization": "Bearer secret1"},
        json={},
    )
    assert response.status_code == 200
    assert (await c.post("/api/library/nodes/n1/operations/claim")).status_code == 401


async def test_rename_replay_after_filesystem_commit(isolated):
    _, c, root = isolated
    fid = await folder(c)
    await ingest(c, fid)
    await worker(root).run_once(c)
    op = (await c.post(f"/api/library/folders/{fid}/rename", json={"name": "Moved"})).json()
    r = await c.post(
        "/api/library/nodes/n1/operations/claim", headers={"Authorization": "Bearer secret1"}
    )
    task = r.json()["operation"]
    w = worker(root)
    apply_operation(w, task)
    record = root / "n1/.hololab-operations" / op["operation_id"] / "state.json"
    state = json.loads(record.read_text())
    state["state"] = "prepared"
    record.write_text(json.dumps(state))
    apply_operation(w, task)  # crash after rename, before final journal acknowledgement
    assert (root / "n1/Moved/clip.mp4").exists()


async def test_download_staging_range_and_relay(isolated):
    app, c, root = isolated
    data = b"0123456789" * 10000
    s = await upload(c, data)
    rid = s["resource_id"]
    url = f"/api/library/resources/{rid}/download"
    r = await c.get(url)
    assert r.status_code == 200 and sha(r.content) == sha(data)
    assert r.headers["x-library-route"] == "gateway_staging"
    r = await c.get(url, headers={"Range": "bytes=10-29"})
    assert r.status_code == 206 and r.content == data[10:30]
    assert (await c.get(url, headers={"Range": "bytes=100001-"})).status_code == 416
    await worker(root).run_once(c)
    await collect(app.state.library)
    pending = asyncio.create_task(c.get(url))
    for _ in range(100):
        if await app.state.library.rows(
            "SELECT 1 FROM library_transfers WHERE action='relay_upload'"
        ):
            break
        await asyncio.sleep(0.02)
    await worker(root).run_once(c)
    r = await pending
    assert r.status_code == 200, r.text
    assert r.headers["x-library-route"] == "gateway_relay" and sha(r.content) == sha(data)
    assert not app.state.library.download_pins


async def test_peer_download_uses_bounded_ranges(isolated, monkeypatch):
    import httpx

    from hololab.gateway import library_download
    from hololab.gateway.library import CHUNK_SIZE
    from hololab.node.fileserver import create_fileserver_app

    app, c, root = isolated
    data = b"x" * (CHUNK_SIZE * 2 + 3)
    session = await upload(c, data)
    await worker(root).run_once(c)
    await collect(app.state.library)
    await app.state.library.execute(
        "UPDATE nodes SET advertised_url='http://peer' WHERE node_id='n1'"
    )
    peer = create_fileserver_app(
        workspace_root=root / "workspace",
        library_root=root / "n1",
        library_token_provider=lambda: "secret1",
    )
    ranges = []

    async def observe(request):
        ranges.append(request.headers["range"])

    original = httpx.AsyncClient
    monkeypatch.setattr(
        library_download.httpx,
        "AsyncClient",
        lambda **kwargs: original(
            **kwargs, transport=httpx.ASGITransport(app=peer), event_hooks={"request": [observe]}
        ),
    )
    response = await c.get("/api/library/resources/" + session["resource_id"] + "/download")
    assert response.status_code == 200 and sha(response.content) == sha(data)
    assert response.headers["x-library-route"] == "peer_direct"
    assert ranges == [
        f"bytes=0-{CHUNK_SIZE - 1}",
        f"bytes={CHUNK_SIZE}-{CHUNK_SIZE * 2 - 1}",
        f"bytes={CHUNK_SIZE * 2}-{len(data) - 1}",
    ]


async def test_pending_namespace_survives_startup_and_refuses_overwrite(isolated):
    app, c, root = isolated
    fid = await folder(c)
    await ingest(c, fid)
    await worker(root).run_once(c)
    (root / "n1/Conflict").mkdir()
    (root / "n1/Conflict/keep").write_text("untouched")
    result = await c.post(f"/api/library/folders/{fid}/rename", json={"name": "Conflict"})
    assert result.status_code == 202
    app.state.library.close()
    await app.state.library.startup()
    await worker(root).run_once(c)
    assert (root / "n1/Original").exists()
    assert (root / "n1/Conflict/keep").read_text() == "untouched"
    operation = (await c.get("/api/library/operations/" + result.json()["operation_id"])).json()
    assert operation["state"] == "pending"
    assert "overwriting" in operation["nodes"][0]["last_error"]


async def test_delete_then_rename_and_delete_with_cancelled_upload_history(isolated):
    app, c, root = isolated
    await app.state.library.execute("UPDATE nodes SET online=0 WHERE node_id='n2'")
    # Real cancellation leaves exactly the terminal tombstone seen in production.
    response = await c.post(
        "/api/library/uploads",
        json={
            "filename": "smoke-test-20mb.bin",
            "size_bytes": 3,
            "sha256": sha(b"old"),
            "target_node_ids": [],
        },
    )
    cancelled = response.json()
    assert (await c.delete("/api/library/uploads/" + cancelled["upload_id"])).status_code == 200
    tombstone = await app.state.library.rows(
        "SELECT lifecycle FROM library_resources WHERE resource_id=?",
        (cancelled["resource_id"],),
    )
    assert tombstone == [{"lifecycle": "deleted"}]
    fid = await folder(c, "mgmt-e2e-demo")
    session = await ingest(c, fid)
    await worker(root).run_once(c)
    assert (await c.delete("/api/library/resources/" + session["resource_id"])).status_code == 202
    # No acknowledgement yet: the guard must still reject a real deletion.
    response = await c.post(f"/api/library/folders/{fid}/rename", json={"name": "Renamed"})
    assert response.status_code == 409
    await worker(root).run_once(c)
    await collect(app.state.library)
    assert (await c.get("/api/library/resources/" + session["resource_id"])).status_code == 404
    # No arbitrary sleep: immediately usable after the final GC acknowledgement.
    response = await c.post(f"/api/library/folders/{fid}/rename", json={"name": "Renamed"})
    assert response.status_code == 202, response.text
    assert [n["node_id"] for n in response.json()["nodes"]] == ["n1"]
    await finish(c, root, response.json())
    assert (root / "n1/Renamed").is_dir()
    assert not (root / "n1/mgmt-e2e-demo").exists()
    response = await c.delete("/api/library/folders/" + fid)
    assert response.status_code == 202, response.text
    await finish(c, root, response.json())
    assert not (root / "n1/Renamed").exists()
    # The fix does not depend on deleting unrelated cancellation history.
    assert await app.state.library.rows(
        "SELECT 1 FROM library_resources WHERE resource_id=? AND lifecycle='deleted'",
        (cancelled["resource_id"],),
    )


async def test_evicted_upload_is_terminal_but_failed_cleanup_still_blocks(isolated):
    app, c, root = isolated
    fid = await folder(c)
    session = await ingest(c, fid)
    await worker(root).run_once(c)
    # Evicting staging preserves a terminal completed upload with no reservation.
    await collect(app.state.library)
    states = await app.state.library.rows(
        "SELECT state,reserved_bytes FROM library_upload_sessions WHERE upload_id=?",
        (session["upload_id"],),
    )
    assert states == [{"state": "completed", "reserved_bytes": 0}]
    response = await c.post(f"/api/library/folders/{fid}/rename", json={"name": "Renamed"})
    assert response.status_code == 202, response.text
    await finish(c, root, response.json())
    assert (root / "n1/Renamed/clip.mp4").exists()
    # A failed deletion is not terminal at the resource level: still needs cleanup.
    (root / "n1/Renamed/clip.mp4").write_bytes(b"modified")
    assert (await c.delete("/api/library/resources/" + session["resource_id"])).status_code == 202
    await worker(root).run_once(c)
    assert (await c.get("/api/library/resources/" + session["resource_id"])).json()[
        "status"
    ] == "deleting"
    assert (
        await c.post(f"/api/library/folders/{fid}/rename", json={"name": "Must not move"})
    ).status_code == 409


async def test_recursive_nested_offline_replay_and_partial_ready(isolated):
    app, c, root = isolated
    lib = app.state.library
    fid = await folder(c, "Tree")
    child = await folder(c, "Child", fid)
    leaf = await folder(c, "Leaf", child)
    first = await ingest(c, fid, targets=["n1", "n2"])
    second = await ingest(c, leaf, name="other.bin")
    # A terminal failed placement is eligible; actively distributing is not.
    blocked = await c.delete(f"/api/library/folders/{fid}?recursive=true")
    assert blocked.status_code == 409
    await worker(root).run_once(c)
    await worker(root).run_once(c)
    await lib.execute(
        "UPDATE library_transfers SET state='failed',last_error='injected offline' WHERE target_node_id='n2'"
    )
    await lib.execute("UPDATE library_placements SET observed_state='failed' WHERE node_id='n2'")
    preview = (await c.get(f"/api/library/folders/{fid}/delete-preview")).json()
    assert (preview["resource_count"], preview["subfolder_count"]) == (2, 2)
    r = await c.delete(f"/api/library/folders/{fid}?recursive=true")
    assert r.status_code == 202, r.text
    oid = r.json()["operation_id"]
    assert (await c.delete(f"/api/library/folders/{fid}?recursive=true")).json()[
        "operation_id"
    ] == oid
    assert (
        await c.post("/api/library/folders", json={"name": "race", "parent_id": fid})
    ).status_code == 409
    assert (
        await c.post(f"/api/library/folders/{child}/rename", json={"name": "race"})
    ).status_code == 409
    # Restart reconciliation must preserve the deletion epochs and GC work.
    await lib.startup()
    for _ in range(4):
        await worker(root).run_once(c)
        await collect(lib)
    assert (await c.get(f"/api/library/operations/{oid}")).json()["state"] == "cascading"
    assert await lib.rows("SELECT 1 FROM library_folders WHERE folder_id=?", (fid,))
    # Reconnected B acknowledges absence; directories then retire bottom-up.
    for _ in range(10):
        await worker(root).run_once(c)
        await worker(root, "n2").run_once(c)
        await collect(lib)
    done = (await c.get(f"/api/library/operations/{oid}")).json()
    assert done["state"] == "completed", done
    assert done["remaining_nodes"] == 0
    assert not (root / "n1/Tree").exists()
    assert not await lib.rows(
        "SELECT 1 FROM library_folders WHERE folder_id IN (?,?,?)", (fid, child, leaf)
    )
    for session in (first, second):
        assert (await c.get("/api/library/resources/" + session["resource_id"])).status_code == 404
    assert (await c.delete(f"/api/library/folders/{fid}?recursive=true")).json()[
        "operation_id"
    ] == oid


async def test_recursive_guards_and_empty_tree(isolated):
    app, c, _root = isolated
    fid = await folder(c)
    assert (await c.delete("/api/library/folders/root?recursive=true")).status_code == 409
    child = await folder(c, "Child", fid)
    assert (await c.delete(f"/api/library/folders/{fid}")).status_code == 409
    r = await c.delete(f"/api/library/folders/{fid}?recursive=true")
    assert r.status_code == 202
    await collect(app.state.library)
    assert (await c.get("/api/library/operations/" + r.json()["operation_id"])).json()[
        "state"
    ] == "completed"
    assert not await app.state.library.rows(
        "SELECT 1 FROM library_folders WHERE folder_id=?", (child,)
    )


async def test_recursive_cleanup_failure_retry_preserves_unknown_local_files(isolated):
    app, c, root = isolated
    lib = app.state.library
    fid = await folder(c, "Protected")
    await ingest(c, fid)
    await worker(root).run_once(c)
    unexpected = root / "n1/Protected/unregistered.txt"
    unexpected.write_text("do not recursively unlink me")
    r = await c.delete(f"/api/library/folders/{fid}?recursive=true")
    oid = r.json()["operation_id"]
    await worker(root).run_once(c)
    await collect(lib)
    for _ in range(5):
        await worker(root).run_once(c)
        await lib.execute(
            "UPDATE library_operation_nodes SET next_retry_ts=0 WHERE state='pending'"
        )
    stalled = (await c.get(f"/api/library/operations/{oid}")).json()
    assert stalled["state"] == "cascading"
    assert stalled["nodes"][0]["state"] == "failed"
    assert stalled["nodes"][0]["last_error"]
    assert unexpected.read_text() == "do not recursively unlink me"
    unexpected.unlink()  # Simulates the owner resolving unexpected local contents.
    assert (await c.post(f"/api/library/operations/{oid}/retry")).status_code == 200
    await worker(root).run_once(c)
    await collect(lib)
    assert (await c.get(f"/api/library/operations/{oid}")).json()["state"] == "completed"
    assert not (root / "n1/Protected").exists()


async def test_recursive_refuses_uploads_and_unfinished_resource_deletion(isolated):
    app, c, root = isolated
    fid = await folder(c)
    upload_response = await c.post(
        "/api/library/uploads",
        json={
            "filename": "unfinished.bin",
            "folder_id": fid,
            "size_bytes": 3,
            "sha256": sha(b"abc"),
            "target_node_ids": ["n1"],
        },
    )
    assert upload_response.status_code == 201
    assert (await c.delete(f"/api/library/folders/{fid}?recursive=true")).status_code == 409
    uid = upload_response.json()["upload_id"]
    assert (await c.delete(f"/api/library/uploads/{uid}")).status_code == 200
    session = await ingest(c, fid)
    await worker(root).run_once(c)
    assert (await c.delete("/api/library/resources/" + session["resource_id"])).status_code == 202
    assert (await c.delete(f"/api/library/folders/{fid}?recursive=true")).status_code == 409
    await worker(root).run_once(c)
    await collect(app.state.library)
    # The cancelled session's tombstone is terminal history, not a permanent guard.
    response = await c.delete(f"/api/library/folders/{fid}?recursive=true")
    assert response.status_code == 202, response.text
    for _ in range(4):
        await worker(root).run_once(c)
        await collect(app.state.library)
    assert (await c.get("/api/library/operations/" + response.json()["operation_id"])).json()[
        "state"
    ] == "completed"


async def test_recursive_remaining_counts_exclude_acknowledged_directories(isolated):
    app, c, root = isolated
    lib = app.state.library
    fid = await folder(c, "Empty on two devices")
    for node in ("n1", "n2"):
        (root / node / "Empty on two devices").mkdir(parents=True)
        await lib.execute("INSERT INTO library_folder_nodes VALUES (?,?)", (fid, node))
    r = await c.delete(f"/api/library/folders/{fid}?recursive=true")
    oid = r.json()["operation_id"]
    await collect(lib)
    await worker(root).run_once(c)
    partial = (await c.get(f"/api/library/operations/{oid}")).json()
    assert partial["remaining_nodes"] == 1
    assert partial["folder_remaining_nodes"][fid] == 1
    assert [n["node_id"] for n in partial["nodes"]] == ["n2"]
    await worker(root, "n2").run_once(c)
    await collect(lib)
    assert (await c.get(f"/api/library/operations/{oid}")).json()["state"] == "completed"
