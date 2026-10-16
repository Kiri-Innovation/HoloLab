"""Temporary payload GC retains ownership proof and never discards resumable work."""

import fcntl
import json
import os
import uuid

import pytest

from hololab.gateway.library_gc import collect
from hololab.node.library_checkpoints import reap, reclaim
from tests.test_library_transfers import claim, upload, worker
from tests.test_library_transfers import setup as setup


async def test_publication_moves_receipt_and_delete_removes_both(setup):
    app, client, root = setup
    session = await upload(client, b"checkpoint ownership")
    task = await claim(client)
    w = worker(root)
    result = await w.materialize(client, task)
    tid = task["transfer_id"]
    assert not (w.root / ".hololab-tmp" / tid).exists()
    receipt = w.root / ".hololab-receipts" / tid
    assert {p.name for p in receipt.iterdir()} == {"owner.lock", "state.json"}
    # A lost Gateway receipt must remain recoverable after the temp directory is gone.
    assert await worker(root).materialize(client, task) == result
    response = await client.post(w.prefix + f"/{tid}/receipt", headers=w.headers(task), json=result)
    assert response.status_code == 200
    await client.delete("/api/library/resources/" + session["resource_id"])
    await w.run_once(client)
    await collect(app.state.library)
    assert not receipt.exists()
    assert list((w.root / ".hololab-tmp").iterdir()) == []
    assert not (w.root / "movie.mp4").exists()


async def test_legacy_completed_checkpoint_is_archived_on_idle(setup):
    _, client, root = setup
    await upload(client, b"legacy")
    w = worker(root)
    await w.run_once(client)
    receipt = next((w.root / ".hololab-receipts").iterdir())
    legacy = w.root / ".hololab-tmp" / receipt.name
    receipt.rename(legacy)
    await reap(w, client)
    assert not legacy.exists()
    assert (receipt / "state.json").is_file()
    assert (w.root / "movie.mp4").read_bytes() == b"legacy"


async def test_failed_and_expired_lease_resume_but_cancelled_checkpoint_is_removed(setup):
    app, client, root = setup
    await upload(client, b"resumable")
    task = await claim(client)
    w = worker(root)
    directory = w.root / ".hololab-tmp" / task["transfer_id"]
    directory.mkdir(parents=True)
    (directory / "payload.part").write_bytes(b"resu")
    (directory / "state.json").write_text(json.dumps({"state": "receiving", "confirmed_bytes": 4}))
    (directory / "owner.lock").touch()
    for state in ("failed", "running"):
        await app.state.library.execute(
            "UPDATE library_transfers SET state=?,lease_until=0 WHERE transfer_id=?",
            (state, task["transfer_id"]),
        )
        await reap(w, client)
        assert (directory / "payload.part").read_bytes() == b"resu"
    await app.state.library.execute(
        "UPDATE library_transfers SET state='cancelled' WHERE transfer_id=?", (task["transfer_id"],)
    )
    fd = os.open(directory / "owner.lock", os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        await reap(w, client)
        assert directory.exists()
    finally:
        os.close(fd)
    await reap(w, client)
    assert not directory.exists()


async def test_checkpoint_state_auth_and_gc_orphans(setup):
    _, client, root = setup
    await upload(client, b"node identity")
    task = await claim(client)
    endpoint = "/api/library/nodes/n2/transfers/checkpoints"
    body = {"transfer_ids": [task["transfer_id"]]}
    assert (await client.post(endpoint, json=body)).status_code == 401
    response = await client.post(endpoint, headers={"Authorization": "Bearer secret2"}, json=body)
    assert response.json()["states"][task["transfer_id"]] == "unknown"
    w = worker(root)
    tid = str(uuid.uuid4())
    orphan = w.root / ".hololab-tmp" / tid
    orphan.mkdir(parents=True)
    (orphan / "owner.lock").touch()
    await reap(w, client)
    assert not orphan.exists()
    assert (
        await client.post(
            endpoint, headers={"Authorization": "Bearer secret2"}, json={"transfer_ids": ["../bad"]}
        )
    ).status_code == 422


def test_unknown_files_and_symlinks_are_not_cleaned(tmp_path):
    tid = str(uuid.uuid4())
    directory = tmp_path / ".hololab-tmp" / tid
    directory.mkdir(parents=True)
    (directory / "payload.part").write_bytes(b"partial")
    (directory / "unexpected.txt").write_text("keep")
    with pytest.raises(ValueError, match="Unknown checkpoint contents"):
        reclaim(tmp_path, tid, "gone")
    assert (directory / "payload.part").exists()
    (directory / "unexpected.txt").unlink()
    (directory / "state.json").symlink_to(tmp_path / "external")
    with pytest.raises(OSError):
        reclaim(tmp_path, tid, "gone")
    assert (directory / "payload.part").exists()


async def test_cancelled_publication_keeps_proof_until_resource_cleanup(setup):
    app, client, root = setup
    session = await upload(client, b"cancel after publication")
    task = await claim(client)
    w = worker(root)
    await w.materialize(client, task)
    receipt = w.root / ".hololab-receipts" / task["transfer_id"]
    legacy = w.root / ".hololab-tmp" / task["transfer_id"]
    receipt.rename(legacy)
    assert (
        await client.delete("/api/library/resources/" + session["resource_id"])
    ).status_code == 202
    await reap(w, client)
    assert (legacy / "state.json").exists()
    await w.run_once(client)
    await collect(app.state.library)
    assert not legacy.exists()
    assert not (w.root / "movie.mp4").exists()
