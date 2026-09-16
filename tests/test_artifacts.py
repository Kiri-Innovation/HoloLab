"""Artifact management: DB soft-delete, liveness classification, list-by-workflow.

Three layers:
  1. HandleBook.mark_deleted + count_by_snapshot round-trips.
  2. NodeRuntime._classify — the ``.hololab-done`` marker-based
     alive/incomplete/dead bucketing.
  3. PATCH-style /api/artifacts endpoint: DB-only rows come back with
     ``state="deleted"|"unknown"``; ``?check=1`` batches a WS round-trip
     against the node.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from hololab.gateway.app import create_app
from hololab.gateway.handles import Handle, HandleBook
from hololab.gateway.registry import NodeSession
from hololab.node.runtime import NodeRuntime
from hololab.protocol.messages import GpuInfo

# ---------------------------------------------------------------------------
# HandleBook — deleted_ts persistence + count rollups
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_deleted_stamps_deleted_ts(tmp_path: Path) -> None:
    """A round-trip through mark_deleted + get returns the timestamp we set."""

    from hololab.persistence.db import open_database

    db = await open_database(tmp_path / "t.sqlite")
    try:
        book = HandleBook(db)
        await book.register(
            Handle(
                handle_id="h1",
                node_id="n1",
                storage="dir",
                tags=["demo_output"],
                path="/tmp/x",
                size_bytes=42,
                created_ts=1000.0,
            )
        )
        # Before delete: deleted_ts is None.
        before = await book.get("h1")
        assert before is not None and before.deleted_ts is None

        await book.mark_deleted("h1", ts=1500.0)
        after = await book.get("h1")
        assert after is not None and after.deleted_ts == 1500.0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_count_by_snapshot_splits_total_vs_deleted(tmp_path: Path) -> None:
    """count_by_snapshot returns {total, deleted} keyed off deleted_ts NULLness."""

    from hololab.persistence.db import open_database

    db = await open_database(tmp_path / "t.sqlite")
    try:
        book = HandleBook(db)

        # Seed one job under a snapshot so the JOIN in count_by_snapshot
        # has something to bind to. The book doesn't own jobs — we write
        # the row directly.
        async def _seed_job(conn):
            await conn.execute(
                """
                INSERT INTO jobs (job_id, workflow_id, snapshot_id, algorithm_name,
                                  algorithm_version, params_json, input_handles_json,
                                  state, created_ts, updated_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "j1",
                    "w1",
                    "snap1",
                    "demo-echo",
                    "0.1.0",
                    "{}",
                    "{}",
                    "done",
                    1000.0,
                    1000.0,
                ),
            )

        await db.write(_seed_job)

        # Three handles: two live, one already user-deleted.
        for hid, deleted in [("h1", False), ("h2", False), ("h3", True)]:
            await book.register(
                Handle(
                    handle_id=hid,
                    node_id="n1",
                    storage="dir",
                    tags=["demo_output"],
                    path=f"/tmp/{hid}",
                    job_id="j1",
                    created_ts=1000.0,
                )
            )
            if deleted:
                await book.mark_deleted(hid)

        counts = await book.count_by_snapshot("snap1")
        assert counts == {"total": 3, "deleted": 1}
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_list_by_workflow_joins_through_jobs(tmp_path: Path) -> None:
    """Handles are surfaced by workflow via the jobs FK, newest first."""

    from hololab.persistence.db import open_database

    db = await open_database(tmp_path / "t.sqlite")
    try:
        book = HandleBook(db)

        async def _seed(conn):
            for i, wid in enumerate(["wA", "wB"]):
                await conn.execute(
                    """
                    INSERT INTO jobs (job_id, workflow_id, algorithm_name,
                                      algorithm_version, params_json,
                                      input_handles_json, state, created_ts,
                                      updated_ts)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"j{i}",
                        wid,
                        "demo-echo",
                        "0.1.0",
                        "{}",
                        "{}",
                        "done",
                        1000.0 + i,
                        1000.0 + i,
                    ),
                )

        await db.write(_seed)

        await book.register(
            Handle(handle_id="hA", node_id="n1", job_id="j0", path="/tmp/a", created_ts=1000.0)
        )
        await book.register(
            Handle(handle_id="hB", node_id="n1", job_id="j1", path="/tmp/b", created_ts=1001.0)
        )

        rows_a = await book.list_by_workflow("wA")
        rows_b = await book.list_by_workflow("wB")
        assert [r.handle_id for r in rows_a] == ["hA"]
        assert [r.handle_id for r in rows_b] == ["hB"]
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# NodeRuntime._classify — filesystem liveness rules
# ---------------------------------------------------------------------------


def _make_runtime(workspace: Path) -> NodeRuntime:
    """Build a minimal NodeRuntime scoped to one temp workspace_root."""

    from hololab.node.config import NodeConfig

    cfg = NodeConfig(
        node_name="test",
        workspace_root=workspace,
        legacy_workspace_roots=[],
    )
    return NodeRuntime(cfg, config_path=workspace / "config.yaml")


def test_classify_dir_with_marker_is_alive(tmp_path: Path) -> None:
    target = tmp_path / "job_dir"
    target.mkdir()
    (target / ".hololab-done").write_text("")
    (target / "output.txt").write_text("hi")

    rt = _make_runtime(tmp_path)
    result = rt._classify(target, "dir")
    assert result.state == "alive"


def test_classify_dir_without_marker_is_incomplete(tmp_path: Path) -> None:
    """Directory present but no ``.hololab-done`` marker = interrupted job."""

    target = tmp_path / "job_dir"
    target.mkdir()
    (target / "partial.txt").write_text("half-written")

    rt = _make_runtime(tmp_path)
    result = rt._classify(target, "dir")
    assert result.state == "incomplete"


def test_classify_file_existing_is_alive(tmp_path: Path) -> None:
    """Single-file handles: existence == completion."""

    target = tmp_path / "artifact.bin"
    target.write_bytes(b"payload")

    rt = _make_runtime(tmp_path)
    result = rt._classify(target, "file")
    assert result.state == "alive"
    assert result.size_bytes == len(b"payload")


def test_classify_missing_path_is_dead(tmp_path: Path) -> None:
    rt = _make_runtime(tmp_path)
    result = rt._classify(tmp_path / "gone", "dir")
    assert result.state == "dead"


def test_delete_missing_path_skips_workspace_guard(tmp_path: Path) -> None:
    """A handle whose file is already gone can be soft-deleted even if the
    recorded path is outside the current workspace roots.

    This is the common cleanup case: the user has already relocated or
    rm'd an old workspace themselves, the DB still points there, and now
    they want the Artifacts page to bucket the row as ``deleted`` instead
    of ``dead``. Since there's nothing left on disk to worry about, the
    workspace-root guard doesn't apply.
    """

    from hololab.protocol.messages import ArtifactDeleteReq

    ws = tmp_path / "ws"
    ws.mkdir()
    rt = _make_runtime(ws)

    sent: list[Any] = []

    async def _fake_send(kind: str, payload: Any) -> None:
        sent.append((kind, payload))

    rt._send = _fake_send  # type: ignore[method-assign]

    import asyncio as _asyncio

    _asyncio.run(
        rt._handle_artifact_delete_req(
            ArtifactDeleteReq(
                req_id="x",
                handle_id="h1",
                path="/nowhere/that/exists/anymore",
                storage="dir",
            )
        )
    )

    assert len(sent) == 1
    kind, payload = sent[0]
    assert kind == "artifact_delete_resp"
    assert payload.ok is True
    assert payload.freed_bytes == 0


def test_path_is_under_workspace_refuses_outside_paths(tmp_path: Path) -> None:
    """Delete safety: a path outside every configured root gets refused."""

    inside = tmp_path / "ws"
    inside.mkdir()
    rt = _make_runtime(inside)

    assert rt._path_is_under_workspace(inside / "some" / "job")
    # Sibling of the workspace — should not be reachable.
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    assert not rt._path_is_under_workspace(outside)


# ---------------------------------------------------------------------------
# REST — GET /api/artifacts + DELETE /api/artifacts/{id}
# ---------------------------------------------------------------------------


def _seed_artifact(client: TestClient, handle: Handle) -> None:
    client.portal.call(client.app.state.handles.register, handle)


def test_list_artifacts_returns_deleted_bucket_without_check(tmp_path: Path) -> None:
    """Without ``?check=1`` the state field is only ``deleted`` or ``pending``.

    ``pending`` is our vocabulary for "we haven't asked the filesystem yet
    — could be alive, incomplete, or dead". The Artifacts page renders it
    as a neutral chip and lets the user opt in to a live check.
    """

    app = create_app(db_path=tmp_path / "test.sqlite")
    with TestClient(app) as client:
        _seed_artifact(
            client,
            Handle(
                handle_id="h-alive",
                node_id="n1",
                storage="dir",
                tags=["demo_output"],
                path="/tmp/never-touched",
                created_ts=1000.0,
            ),
        )
        _seed_artifact(
            client,
            Handle(
                handle_id="h-gone",
                node_id="n1",
                storage="dir",
                tags=["demo_output"],
                path="/tmp/also-untouched",
                created_ts=999.0,
                deleted_ts=1200.0,
            ),
        )

        r = client.get("/api/artifacts")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total_rows"] == 2
        assert body["checked"] is False
        by_id = {row["handle_id"]: row for row in body["rows"]}
        assert by_id["h-alive"]["state"] == "pending"
        assert by_id["h-gone"]["state"] == "deleted"


def test_delete_artifact_is_idempotent_for_already_deleted(tmp_path: Path) -> None:
    """Deleting a handle whose ``deleted_ts`` is already set returns 200 OK
    without asking the node — the file is presumed gone by definition."""

    app = create_app(db_path=tmp_path / "test.sqlite")
    with TestClient(app) as client:
        _seed_artifact(
            client,
            Handle(
                handle_id="h1",
                node_id="n1",
                storage="dir",
                tags=["demo_output"],
                path="/tmp/x",
                created_ts=1000.0,
                deleted_ts=1500.0,
            ),
        )
        r = client.delete("/api/artifacts/h1")
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "deleted"
        assert body["freed_bytes"] == 0


def test_delete_artifact_404_when_unknown_handle(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "test.sqlite")
    with TestClient(app) as client:
        r = client.delete("/api/artifacts/does-not-exist")
        assert r.status_code == 404


def test_delete_artifact_tombstones_when_node_offline(tmp_path: Path) -> None:
    """When the producing node is offline we can't touch the on-disk
    file, but the row is what the Artifacts page needs to hide. The
    delete flow tombstones the DB row and returns a ``note`` so the
    caller understands nothing was removed from disk.

    This is the common cleanup path for handles produced by a node the
    user has since decommissioned or moved (workspace_root migration
    without adding a legacy root)."""

    app = create_app(db_path=tmp_path / "test.sqlite")
    with TestClient(app) as client:
        _seed_artifact(
            client,
            Handle(
                handle_id="h1",
                node_id="n1",
                storage="dir",
                tags=["demo_output"],
                path="/tmp/x",
                created_ts=1000.0,
            ),
        )
        # Registry is empty — no session for n1.
        r = client.delete("/api/artifacts/h1")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["state"] == "deleted"
        assert body["freed_bytes"] == 0
        assert "note" in body

        # DB row now carries deleted_ts.
        row = client.portal.call(client.app.state.handles.get, "h1")
        assert row is not None and row.deleted_ts is not None


def test_delete_artifact_hits_node_and_marks_row(tmp_path: Path) -> None:
    """End-to-end DELETE: gateway sends artifact_delete_req to the node,
    receives ok=True, then stamps deleted_ts on the DB row.

    Fakes the node WS by intercepting send_text on the mock session and
    resolving the pending future with a synthetic success reply.
    """

    app = create_app(db_path=tmp_path / "test.sqlite")
    with TestClient(app) as client:
        _seed_artifact(
            client,
            Handle(
                handle_id="h1",
                node_id="n1",
                storage="dir",
                tags=["demo_output"],
                path="/tmp/x",
                created_ts=1000.0,
            ),
        )

        async def _fake_send(raw: str) -> None:
            env = json.loads(raw)
            req_id = env["payload"]["req_id"]
            fut = client.app.state.pending_artifact_deletes[req_id]
            fut.set_result(
                {
                    "req_id": req_id,
                    "handle_id": "h1",
                    "ok": True,
                    "freed_bytes": 4096,
                    "error": None,
                }
            )

        session = NodeSession(
            node_id="n1",
            session_id="sess",
            node_name="n1",
            ws=MagicMock(),
            advertised_url=None,
            gpu=GpuInfo(),
            packs=[],
            protocol_v=1,
            workspace_root="/tmp",
        )
        session.ws.send_text = _fake_send
        app.state.registry._sessions["n1"] = session

        r = client.delete("/api/artifacts/h1")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["state"] == "deleted"
        assert body["freed_bytes"] == 4096

        # DB row must now carry deleted_ts.
        row = client.portal.call(client.app.state.handles.get, "h1")
        assert row is not None and row.deleted_ts is not None
