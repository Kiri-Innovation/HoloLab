"""Storage reachability regressions, using real files and real symlinks.

Only the node transport is simulated: its delete acknowledgement actually
removes the requested temporary path. No production DB, node or GPU is used.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from hololab.array_positions import encode_position
from hololab.gateway.app import create_app
from hololab.gateway.artifacts import delete_artifact
from hololab.gateway.handles import Handle
from hololab.gateway.jobs import Job, JobState
from hololab.gateway.registry import NodeSession
from hololab.gateway.snapshot_delete import delete_snapshot
from hololab.gateway.workflows import GraphNode, WorkflowGraph
from hololab.protocol.messages import GpuInfo


class Storage:
    def __init__(self, client, root):
        self.client = client
        self.app = client.app
        self.root = root
        self.deleted = []
        self.fail = False
        self.delay = None
        ws = MagicMock()
        ws.send_text = self.send
        self.session = NodeSession(
            node_id="node",
            session_id="session",
            node_name="node",
            ws=ws,
            advertised_url=None,
            gpu=GpuInfo(),
            packs=[],
            protocol_v=1,
            workspace_root=str(root),
        )
        self.app.state.registry._sessions["node"] = self.session

    async def send(self, raw):
        frame = json.loads(raw)
        if frame["kind"] != "artifact_delete_req":
            return
        payload = frame["payload"]
        if self.delay:
            await self.delay()
        freed = 0
        if not self.fail:
            p = Path(payload["path"])
            assert p.is_relative_to(self.root)
            if p.is_file() and not p.is_symlink():
                freed = p.stat().st_size
            elif p.is_dir() and not p.is_symlink():
                freed = sum(
                    q.stat().st_size for q in p.rglob("*") if q.is_file() and not q.is_symlink()
                )
            if p.is_symlink() or p.is_file():
                p.unlink(missing_ok=True)
            elif p.is_dir():
                shutil.rmtree(p)
            self.deleted.append(payload["path"])
        self.app.state.pending_artifact_deletes[payload["req_id"]].set_result(
            {
                "ok": not self.fail,
                "freed_bytes": freed,
                "error": "disk refused" if self.fail else None,
            }
        )

    async def snapshot(self):
        graph = WorkflowGraph(
            nodes=[GraphNode(id="A", algorithm_name="demo-echo", algorithm_version="0.1.0")],
            edges=[],
        )
        await self.app.state.workflows.save_draft(workflow_id="workflow", name="test", graph=graph)
        return (
            await self.app.state.workflows.create_snapshot(workflow_id="workflow", graph=graph)
        ).snapshot_id

    async def job(self, jid, sid, *, parent=None, reused=None, inputs=None, state=JobState.DONE):
        job = Job(
            job_id=jid,
            workflow_id="workflow",
            snapshot_id=sid,
            graph_node_id="A",
            algorithm_name="demo-echo",
            algorithm_version="0.1.0",
            node_id="node",
            state=state,
            parent_job_id=parent,
            reused_from_job_id=reused,
            input_handles=inputs or {},
        )
        await self.app.state.jobs_store.create(job)
        return job

    async def handle(self, hid, jid, path, port="images", *, storage="dir", size=5):
        h = Handle(
            handle_id=hid,
            job_id=jid,
            node_id="node",
            path=str(path),
            output_port_name=port,
            storage=storage,
            size_bytes=size,
        )
        await self.app.state.handles.register(h)
        return h

    async def family(self, *, n=3, ports=("images",), aggregate=True, state=JobState.DONE, batch=1):
        old, new = await self.snapshot(), await self.snapshot()
        await self.job("parent", old, state=state)
        leaves = []
        for i in range((n + batch - 1) // batch):
            await self.job(f"shard-{i}", old, parent="parent")
            for e in range(i * batch, min(n, (i + 1) * batch)):
                for port in ports:
                    path = self.root / "parent" / port / encode_position(e)
                    path.mkdir(parents=True)
                    (path / "image.png").write_bytes(b"image")
                    leaves.append(path / "image.png")
                    await self.handle(f"{port}-{e}", f"shard-{i}", path, port)
        if aggregate:
            for port in ports:
                await self.handle(port, "parent", self.root / "parent" / port, port, size=n * 5)
        return old, new, leaves

    def run(self, fn):
        return self.client.portal.call(fn)

    def preview(self, sid):
        resp = self.client.get(f"/api/snapshots/{sid}/deletion-preview")
        assert resp.status_code == 200, resp.text
        return resp.json()

    def delete(self, sid):
        return self.client.delete(f"/api/snapshots/{sid}")


@pytest.fixture
def storage(tmp_path):
    app = create_app(db_path=tmp_path / "isolated.sqlite")
    with TestClient(app) as client:
        yield Storage(client, tmp_path)


def test_parent_only_inheritance_preserves_80_real_files_and_links_then_reclaims(storage):
    s = storage

    async def seed():
        old, new, leaves = await s.family(n=80)
        await s.app.state.snapshot_jobs.attribute(new, "parent", "A")
        links = s.root / "consumer"
        links.mkdir()
        for i, leaf in enumerate(leaves):
            (links / f"{i}.png").symlink_to(leaf)
        return old, new, leaves, list(links.iterdir())

    old, new, leaves, links = s.run(seed)
    preview = s.preview(old)
    assert preview["artifacts"] == {"exclusive_count": 0, "shared_count": 1, "exclusive_bytes": 0}
    assert preview["jobs"]["shared_count"] == 81
    result = s.delete(old)
    assert result.status_code == 200, result.text
    assert s.deleted == []
    assert all(p.read_bytes() == b"image" for p in leaves + links)
    assert s.run(lambda: s.app.state.jobs_store.get("shard-79")) is not None
    handles = s.run(lambda: s.app.state.handles.list_all())
    assert all(h.deleted_ts is None for h in handles)
    listing = s.client.get("/api/artifacts", params={"snapshot_id": new}).json()
    assert listing["total_rows"] == 1 and listing["total_bytes"] == 400
    assert listing["rows"][0]["element_count"] == 80
    assert new in listing["rows"][0]["snapshot_ids"]
    details = s.client.get(
        "/api/artifacts", params={"snapshot_id": new, "include_elements": True}
    ).json()
    assert details["total_rows"] == 81
    last_preview = s.preview(new)
    assert last_preview["artifacts"]["exclusive_count"] == 1
    last_delete = s.delete(new)
    assert last_delete.status_code == 200
    assert last_delete.json()["freed_bytes"] == last_preview["artifacts"]["exclusive_bytes"] == 400
    assert s.deleted == [str(s.root / "parent" / "images")]
    assert not any(p.exists() for p in leaves)
    assert all(h.deleted_ts is not None for h in s.run(lambda: s.app.state.handles.list_all()))
    assert s.run(lambda: s.app.state.jobs_store.get("parent")) is None
    print(
        "EVIDENCE: 80/80 real symlinks readable after source snapshot delete; "
        "81 family jobs retained; 1 logical output / 400 bytes; "
        "last reference -> 1 physical delete, 81 tombstones"
    )


@pytest.mark.parametrize("reference", ["parent", "shard-0", "legacy-chain"])
def test_historical_membership_and_repeated_inheritance(storage, reference):
    s = storage

    async def seed():
        old, new, leaves = await s.family()
        if reference == "legacy-chain":
            await s.job("reuse-1", new, reused="shard-0")
            third = await s.snapshot()
            await s.job("reuse-2", third, reused="reuse-1")
        else:
            await s.app.state.snapshot_jobs.attribute(new, reference, "A")
            third = await s.snapshot()
            await s.app.state.snapshot_jobs.attribute(third, reference, "A")
        return old, new, third, leaves

    old, new, third, leaves = s.run(seed)
    assert s.delete(old).status_code == 200
    assert s.delete(new).status_code == 200
    assert all(p.read_bytes() == b"image" for p in leaves)
    assert not s.deleted
    assert s.delete(third).status_code == 200
    assert len(s.deleted) == 1


@pytest.mark.parametrize("state", [JobState.DONE, JobState.FAILED, JobState.CANCELLED])
@pytest.mark.parametrize("aggregate", [False, True])
def test_multiple_ports_batched_elements_and_partial_outputs(storage, state, aggregate):
    s = storage
    old, _, leaves = s.run(
        lambda: s.family(n=5, batch=2, ports=("images", "masks"), state=state, aggregate=aggregate)
    )
    listed = s.client.get("/api/artifacts", params={"snapshot_id": old}).json()
    assert listed["total_rows"] == (2 if aggregate else 10)
    assert listed["total_bytes"] == 50
    assert any(r["partial"] for r in listed["rows"]) is (not aggregate)
    preview = s.preview(old)
    assert preview["artifacts"]["exclusive_count"] == listed["total_rows"]
    assert preview["artifacts"]["exclusive_bytes"] == 50
    cache = MagicMock()
    s.app.state.summary_cache = cache
    response = s.delete(old)
    assert response.status_code == 200, response.text
    assert response.json()["artifacts_removed_from_disk"] == (2 if aggregate else 10)
    assert response.json()["freed_bytes"] == preview["artifacts"]["exclusive_bytes"] == 50
    assert not any(p.exists() for p in leaves)
    rows = s.run(lambda: s.app.state.handles.list_all())
    assert all(h.deleted_ts is not None for h in rows)
    assert {c.args[0] for c in cache.invalidate.call_args_list} == {h.handle_id for h in rows}


@pytest.mark.parametrize("entry", ["single-parent", "single-element", "bulk"])
def test_all_cleanup_entries_protect_shared_family(storage, entry):
    s = storage

    async def seed():
        old, new, leaves = await s.family()
        await s.app.state.snapshot_jobs.attribute(new, "parent", "A")
        return old, new, leaves

    old, _, leaves = s.run(seed)
    if entry == "bulk":
        result = s.client.post("/api/artifacts/delete-bulk", json={"snapshot_id": old})
        assert result.status_code == 200
        assert result.json()["results"][0]["state"] == "error"
    else:
        hid = "images" if entry == "single-parent" else "images-0"
        assert s.client.delete(f"/api/artifacts/{hid}").status_code == 409
    assert all(p.read_bytes() == b"image" for p in leaves)
    assert not s.deleted


def test_indirect_input_view_and_downstream_symlink_protect_storage_and_lineage(storage):
    s = storage

    async def seed():
        old, new, leaves = await s.family()
        view = await s.handle("input-view", None, leaves[0].parent, None)
        await s.job("consumer", new, inputs={"images": view.handle_id})
        output = s.root / "downstream"
        output.mkdir()
        (output / "image.png").symlink_to(leaves[0])
        await s.handle("consumer-out", "consumer", output, "out")
        return old, new, leaves, output

    old, new, leaves, output = s.run(seed)
    assert s.preview(old)["artifacts"]["exclusive_count"] == 0
    assert s.delete(old).status_code == 200
    assert (output / "image.png").read_bytes() == b"image"
    assert all(p.exists() for p in leaves)
    lineage = s.client.get("/api/artifacts/images-0/lineage", params={"direction": "down"}).json()
    assert lineage["root"] == "images"
    assert any(e["parent"] == "images" and e["child"] == "consumer-out" for e in lineage["edges"])
    assert s.client.delete("/api/artifacts/images-0").status_code == 409
    # Removing the last consumer releases its transitive dependency. The
    # retained producer can then be reclaimed from the artifact inventory.
    assert s.delete(new).status_code == 200
    assert s.client.delete("/api/artifacts/images-0").status_code == 200
    assert s.deleted[-1] == str(s.root / "parent" / "images")
    assert s.run(lambda: s.app.state.handles.get("input-view")).deleted_ts is not None


def test_different_handle_alias_on_same_node_protects_storage(storage):
    s = storage

    async def seed():
        old, new, leaves = await s.family()
        await s.job("alias-owner", new)
        await s.handle("alias", "alias-owner", leaves[0].parent)
        return old, leaves

    old, leaves = s.run(seed)
    assert s.preview(old)["artifacts"]["shared_count"] == 1
    assert s.delete(old).status_code == 200
    assert leaves[0].read_bytes() == b"image"
    assert not s.deleted


def test_orphan_producer_is_retained_not_assigned_by_path(storage):
    s = storage

    async def seed():
        old, _, leaves = await s.family()
        # A surviving orphan address overlaps the aggregate. It cannot be
        # assigned to the family by directory naming, nor safely reclaimed.
        await s.handle("orphan", "already-purged-job", leaves[0].parent)
        return old, leaves

    old, leaves = s.run(seed)
    assert s.client.delete("/api/artifacts/orphan").status_code == 409
    assert s.preview(old)["artifacts"]["exclusive_count"] == 0
    assert s.delete(old).status_code == 200
    assert all(p.exists() for p in leaves)
    assert s.run(lambda: s.app.state.handles.get("orphan")).deleted_ts is None


@pytest.mark.parametrize("failure", ["offline", "rpc", "send"])
def test_failed_cleanup_keeps_membership_and_can_retry(storage, failure):
    s = storage
    old, _, leaves = s.run(lambda: s.family())
    if failure == "offline":
        s.app.state.registry._sessions.pop("node")
    elif failure == "send":
        s.session.ws.send_text = AsyncMock(side_effect=OSError("connection dropped"))
    else:
        s.fail = True
    response = s.delete(old)
    assert response.status_code == (409 if failure == "offline" else 502)
    assert all(p.read_bytes() == b"image" for p in leaves)
    assert s.run(lambda: s.app.state.workflows.get_snapshot(old)) is not None
    assert s.run(lambda: s.app.state.handles.get("images")).deleted_ts is None
    s.fail = False
    s.session.ws.send_text = s.send
    s.app.state.registry._sessions["node"] = s.session
    assert s.delete(old).status_code == 200
    assert len(s.deleted) == 1


def test_partial_rpc_success_retries_only_remaining_roots(storage):
    s = storage
    old, _, _ = s.run(lambda: s.family(ports=("images", "masks")))
    send = s.send
    calls = 0

    async def flaky(raw):
        nonlocal calls
        calls += 1
        s.fail = calls == 2
        await send(raw)

    s.session.ws.send_text = flaky
    assert s.delete(old).status_code == 502
    assert len(s.deleted) == 1
    assert s.run(lambda: s.app.state.workflows.get_snapshot(old)) is not None
    s.session.ws.send_text = send
    s.fail = False
    assert s.delete(old).status_code == 200
    assert len(s.deleted) == 2 and len(set(s.deleted)) == 2


def test_delete_wins_race_stale_inheritance_is_rejected(storage):
    s = storage
    old, new, _ = s.run(lambda: s.family())

    async def race():
        entered, release = asyncio.Event(), asyncio.Event()

        async def delay():
            entered.set()
            await release.wait()

        s.delay = delay
        deletion = asyncio.create_task(delete_snapshot(s.app, old))
        await asyncio.wait_for(entered.wait(), 3)
        inheritance = asyncio.create_task(s.app.state.snapshot_jobs.attribute(new, "parent", "A"))
        # The durable claim rejects promptly; node writes/ACKs must not wait
        # on a cleanup lock that is held across network I/O.
        with pytest.raises(HTTPException) as error:
            await asyncio.wait_for(inheritance, 3)
        assert error.value.status_code == 409
        release.set()
        await deletion
        assert await s.app.state.snapshot_jobs.get_job_at(new, "A") is None
        assert s.app.state.pending_artifact_deletes == {}

    s.run(race)


def test_inheritance_wins_race_delete_keeps_storage(storage):
    s = storage
    old, new, leaves = s.run(lambda: s.family())

    async def race():
        entered, release = asyncio.Event(), asyncio.Event()

        async def inherit(conn):
            entered.set()
            await release.wait()
            await conn.execute("INSERT INTO snapshot_jobs VALUES (?,?,?)", (new, "parent", "A"))

        inheritance = asyncio.create_task(s.app.state.db.write(inherit))
        await asyncio.wait_for(entered.wait(), 3)
        deletion = asyncio.create_task(delete_snapshot(s.app, old))
        await asyncio.sleep(0)
        assert not deletion.done()
        release.set()
        await inheritance
        await deletion

    s.run(race)
    assert not s.deleted
    assert all(p.read_bytes() == b"image" for p in leaves)


def test_single_cleanup_without_snapshot_normalizes_slice_and_tombstones_views(storage):
    s = storage

    async def seed():
        old, _, leaves = await s.family()
        await s.handle("view", None, leaves[0].parent, None)

        # Simulate an unreferenced retained result, without removing jobs.
        async def remove_membership(conn):
            await conn.execute("DELETE FROM snapshot_jobs WHERE snapshot_id=?", (old,))
            await conn.execute("DELETE FROM snapshots WHERE snapshot_id=?", (old,))

        await s.app.state.db.write(remove_membership)
        return leaves

    leaves = s.run(seed)
    assert s.client.delete("/api/artifacts/images-0").status_code == 200
    assert s.deleted == [str(s.root / "parent" / "images")]
    assert all(h.deleted_ts is not None for h in s.run(lambda: s.app.state.handles.list_all()))
    assert not any(p.exists() for p in leaves)


def test_rpc_timeout_leaves_retryable_rows_and_cleans_pending_future(storage):
    s = storage
    old, _, leaves = s.run(lambda: s.family())

    async def attempt():
        # Ignore the request (lost reply) without spending the production timeout.
        s.session.ws.send_text = AsyncMock()
        h = await s.app.state.handles.get("images")
        with pytest.raises(HTTPException) as error:
            await delete_artifact(s.app, s.session, h, timeout_s=0.01, excluding_snapshot=old)
        assert error.value.status_code == 504
        assert s.app.state.pending_artifact_deletes == {}
        assert (await s.app.state.handles.get("images")).deleted_ts is None

    s.run(attempt)
    assert all(p.exists() for p in leaves)


def test_lost_ack_claim_survives_new_gateway_and_blocks_inheritance(storage):
    s = storage
    old, new, leaves = s.run(lambda: s.family())

    async def lose_ack(raw):
        payload = json.loads(raw)["payload"]
        shutil.rmtree(payload["path"])  # Real removal, but no acknowledgement.

    async def attempt():
        s.session.ws.send_text = lose_ack
        h = await s.app.state.handles.get("images")
        with pytest.raises(HTTPException) as error:
            await delete_artifact(s.app, s.session, h, timeout_s=0.01, excluding_snapshot=old)
        assert error.value.status_code == 504

    s.run(attempt)
    assert not any(p.exists() for p in leaves)
    # A fresh gateway reads the persisted fence; an in-memory lock alone
    # cannot protect references after timeout or process replacement.
    restarted = create_app(db_path=s.root / "isolated.sqlite")
    with TestClient(restarted) as client:

        async def inherit():
            with pytest.raises(HTTPException, match="cleanup is pending"):
                await restarted.state.snapshot_jobs.attribute(new, "parent", "A")

        client.portal.call(inherit)
    s.session.ws.send_text = s.send
    assert s.delete(old).status_code == 200
    assert s.run(lambda: s.app.state.handles.get("images")).deleted_ts is not None


def test_pending_claim_blocks_new_input_view_and_legacy_reuse(storage):
    s = storage
    old, new, leaves = s.run(lambda: s.family())

    async def attempt():
        s.session.ws.send_text = AsyncMock()
        h = await s.app.state.handles.get("images")
        with pytest.raises(HTTPException):
            await delete_artifact(s.app, s.session, h, timeout_s=0.01, excluding_snapshot=old)
        await s.handle("late-view", None, leaves[0].parent, None)
        with pytest.raises(HTTPException, match="cleanup is pending"):
            await s.job("new-consumer", new, inputs={"images": "late-view"})
        with pytest.raises(HTTPException, match="cleanup is pending"):
            await s.job("late-reuse", new, reused="parent")

    s.run(attempt)


def test_orphaned_child_blocks_snapshot_delete(storage):
    s = storage
    old, _, _ = s.run(lambda: s.family())

    async def orphan():
        async def write(conn):
            await conn.execute("UPDATE jobs SET state='orphaned' WHERE job_id='shard-1'")

        await s.app.state.db.write(write)

    s.run(orphan)
    assert s.preview(old)["blocked"] is True
    assert s.delete(old).status_code == 409
    assert not s.deleted


def test_path_overlap_is_node_qualified(storage):
    s = storage

    async def seed():
        old, new, leaves = await s.family()
        await s.job("remote", new)
        await s.app.state.handles.register(
            Handle(
                handle_id="remote-out",
                job_id="remote",
                node_id="other-node",
                storage="dir",
                path=str(leaves[0].parent),
                output_port_name="images",
            )
        )
        return old

    old = s.run(seed)
    assert s.preview(old)["artifacts"]["exclusive_count"] == 1
    assert s.delete(old).status_code == 200
    assert len(s.deleted) == 1


def test_legacy_reuse_slot_resolves_aggregate_parent(storage):
    s = storage

    async def seed():
        _, new, _ = await s.family()
        await s.job("reused", new, reused="shard-0")
        return new

    new = s.run(seed)
    assert s.run(lambda: s.app.state.snapshot_jobs.get_job_at(new, "A")) == "parent"
    listed = s.client.get("/api/artifacts", params={"snapshot_id": new}).json()
    assert listed["total_rows"] == 1 and listed["rows"][0]["handle_id"] == "images"


def test_node_progress_can_commit_before_delete_ack(storage):
    """An unrelated job's progress frame precedes ACK on the same socket."""
    s = storage
    old, _, _ = s.run(lambda: s.family())

    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()

        async def delay():
            entered.set()
            await release.wait()

        s.delay = delay
        deletion = asyncio.create_task(delete_snapshot(s.app, old))
        await asyncio.wait_for(entered.wait(), 3)

        async def progress(conn):
            await conn.execute("UPDATE jobs SET updated_ts=updated_ts+1 WHERE job_id='parent'")

        # This is a distinct task, like the node websocket's receive loop.
        await asyncio.wait_for(asyncio.create_task(s.app.state.db.write(progress)), 3)
        release.set()
        await deletion

    s.run(exercise)


def test_cancelled_delete_keeps_durable_fence_and_retry_works(storage):
    s = storage
    old, new, _ = s.run(lambda: s.family())

    async def exercise():
        entered = asyncio.Event()
        waiting = asyncio.Event()

        async def delay():
            entered.set()
            await waiting.wait()

        s.delay = delay
        deletion = asyncio.create_task(delete_snapshot(s.app, old))
        await asyncio.wait_for(entered.wait(), 3)
        deletion.cancel()
        with pytest.raises(asyncio.CancelledError):
            await deletion
        with pytest.raises(HTTPException, match="cleanup is pending"):
            await s.app.state.snapshot_jobs.attribute(new, "parent", "A")
        # Cancellation during websocket.send_text must clean its pending ACK.
        assert not s.app.state.pending_artifact_deletes
        s.delay = None
        await delete_snapshot(s.app, old)

    s.run(exercise)


def test_bulk_unreferenced_cleanup_deduplicates_aggregate_and_slices(storage):
    s = storage

    async def seed():
        old, _, leaves = await s.family()

        async def release(conn):
            await conn.execute("DELETE FROM snapshots WHERE snapshot_id=?", (old,))

        await s.app.state.db.write(release)
        return leaves

    leaves = s.run(seed)
    response = s.client.post("/api/artifacts/delete-bulk", json={"workflow_id": "workflow"})
    assert response.status_code == 200, response.text
    assert response.json()["requested"] == 1
    assert response.json()["results"][0]["state"] == "deleted"
    assert s.deleted == [str(s.root / "parent" / "images")]
    assert not any(p.exists() for p in leaves)


def test_missing_parent_with_retained_shards_is_not_reconstructed_from_path(storage):
    s = storage
    old, _, leaves = s.run(lambda: s.family())

    async def remove_parent():
        async def write(conn):
            await conn.execute("DELETE FROM jobs WHERE job_id='parent'")

        await s.app.state.db.write(write)

    s.run(remove_parent)
    assert s.preview(old)["artifacts"]["exclusive_count"] == 0
    assert s.client.delete("/api/artifacts/images-0").status_code == 409
    assert s.delete(old).status_code == 200
    assert all(p.read_bytes() == b"image" for p in leaves)
    assert not s.deleted


def test_artifact_summary_and_internal_view_bytes_are_not_duplicated(storage):
    s = storage
    old, _, _ = s.run(lambda: s.family(n=4, batch=2, ports=("images", "masks")))
    summary = s.client.get("/api/artifacts/summary").json()
    assert summary["total_count"] == 2 and summary["total_bytes"] == 40
    details = s.client.get("/api/artifacts", params={"include_elements": True}).json()
    assert details["total_rows"] == 10 and details["total_bytes"] == 40
    counts = s.run(lambda: s.app.state.handles.count_by_snapshot(old))
    assert counts == {"total": 2, "deleted": 0}
    assert all(row["deletion_blocked_reason"] for row in details["rows"])


def test_pending_constant_cleanup_cannot_gain_new_snapshot_binding(storage):
    s = storage

    async def exercise():
        from hololab.constants import constant_value

        await s.snapshot()
        digest, value = constant_value("constant-float-array", {"items": [1]})
        path = s.root / "constant-cache"
        path.mkdir()
        h = await s.handle("constant-handle", None, path, "offsets")

        async def cache(conn):
            await conn.execute(
                "INSERT INTO constant_values VALUES (?,?)", (digest, json.dumps(value))
            )
            await conn.execute(
                "INSERT INTO constant_materializations VALUES (?,?,?)",
                (digest, "node", h.handle_id),
            )

        await s.app.state.db.write(cache)
        s.session.ws.send_text = AsyncMock()
        with pytest.raises(HTTPException):
            await delete_artifact(s.app, s.session, h, timeout_s=0.01)
        graph = WorkflowGraph(
            nodes=[
                GraphNode(
                    id="constant",
                    kind="intrinsic",
                    operation="constant-float-array",
                    params={"items": [1]},
                )
            ],
            edges=[],
        )
        with pytest.raises(HTTPException, match="constant cleanup is pending"):
            await s.app.state.workflows.create_snapshot(workflow_id="workflow", graph=graph)
        s.session.ws.send_text = s.send
        await delete_artifact(s.app, s.session, h)
        # Values are independent of their physical caches: after acknowledged
        # cleanup a new snapshot can bind the value and ensure it afresh.
        snapshot = await s.app.state.workflows.create_snapshot(workflow_id="workflow", graph=graph)
        assert snapshot.snapshot_id

    s.run(exercise)


def test_standalone_surviving_result_also_protects_its_input(storage):
    s = storage

    async def seed():
        old, _, leaves = await s.family()
        await s.job("standalone", None, inputs={"images": "images"})
        output = s.root / "standalone"
        output.mkdir()
        (output / "image.png").symlink_to(leaves[0])
        await s.handle("standalone-out", "standalone", output, "out")
        return old, output

    old, output = s.run(seed)
    assert s.delete(old).status_code == 200
    assert (output / "image.png").read_bytes() == b"image"
    assert s.client.delete("/api/artifacts/images").status_code == 409
    assert s.client.delete("/api/artifacts/standalone-out").status_code == 200
    assert s.client.delete("/api/artifacts/images").status_code == 200


def test_results_being_deleted_together_do_not_pin_each_other(storage):
    s = storage

    async def seed():
        old, _, leaves = await s.family()
        await s.job("consumer", old, inputs={"images": "images"})
        output = s.root / "consumer"
        output.mkdir()
        (output / "image.png").symlink_to(leaves[0])
        await s.handle("consumer-out", "consumer", output, "out")
        return old

    old = s.run(seed)
    assert s.preview(old)["artifacts"]["exclusive_count"] == 2
    response = s.delete(old)
    assert response.status_code == 200, response.text
    assert len(s.deleted) == 2
