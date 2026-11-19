"""Fan-out and WebSocket recovery against real competing WAL connections."""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing

from fastapi.testclient import TestClient

from hololab.gateway.app import _dispatch_node_frame, create_app
from hololab.gateway.execution import run_snapshot
from hololab.gateway.jobs import Job, JobState
from hololab.protocol import JobAck, JobDone, JobLog, JobStarted, decode, encode
from tests.test_fanout_parallelism import (
    _fake_online_node,
    _install_fake_catalog,
    _seed_fanout_graph,
)


async def _stop_library_pollers(app):
    """Keep unrelated library maintenance out of these deterministic schedules."""
    for name in ("library_gc_task", "library_scheduler_task"):
        task = getattr(app.state, name)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        setattr(app.state, name, None)


def test_fifty_shards_complete_with_competing_wal_writer(tmp_path):
    app = create_app(db_path=tmp_path / "fanout.sqlite")
    attempts = []
    dispatched = []

    with TestClient(app) as client:
        client.portal.call(_stop_library_pollers, app)

        async def receive_assignment(frame):
            envelope, assignment = decode(frame)
            assert envelope.kind == "job_assign"
            dispatched.append(assignment.job_id)
            for kind, payload in (
                ("job_ack", JobAck(job_id=assignment.job_id)),
                ("job_started", JobStarted(job_id=assignment.job_id, pid=123)),
                ("job_log", JobLog(job_id=assignment.job_id, stream="stdout", lines=["started"])),
                ("job_done", JobDone(job_id=assignment.job_id, output_handles={})),
            ):
                await _dispatch_node_frame(
                    kind,
                    payload,
                    session,
                    app.state.jobs_store,
                    app.state.handles,
                    app.state.hub,
                    app.state.registry,
                    app.state.logs,
                    app,
                )

        session = _fake_online_node(app, workspace_root=tmp_path, send_hook=receive_assignment)
        session.content_identity = True
        _install_fake_catalog(app)

        def competing_write(statement):
            if (
                not statement.startswith("INSERT INTO job_code_identities")
                or len(dispatched) < 6
                or attempts
            ):
                return
            with closing(sqlite3.connect(app.state.db._path, timeout=0)) as competitor:
                try:
                    with competitor:
                        competitor.execute("INSERT INTO contention_probe DEFAULT VALUES")
                except sqlite3.OperationalError as error:
                    attempts.append(str(error))
                else:
                    attempts.append("committed between identity read and write")

        async def run():
            async def create_probe(conn):
                await conn.execute("CREATE TABLE contention_probe (value INTEGER PRIMARY KEY)")

            await app.state.db.write(create_probe)
            snapshot_id, graph, _ = await _seed_fanout_graph(
                app, element_ids=[f"element-{index:02}" for index in range(50)], parallelism=5
            )
            graph.nodes[1].code_identity = {"sha256": "fixture-content"}
            await app.state.db._writer_conn.set_trace_callback(competing_write)
            try:
                await asyncio.wait_for(
                    run_snapshot(
                        app,
                        snapshot_id=snapshot_id,
                        graph=graph,
                        workflow_id="wf1",
                        skip_graph_nodes={"src"},
                        seed_outputs={"src": {"out": "h-arr"}},
                        job_timeout_s=5,
                    ),
                    timeout=15,
                )
            finally:
                await app.state.db._writer_conn.set_trace_callback(None)

            rows = await app.state.jobs_store.list_recent(limit=100)
            shards = [row for row in rows if row.get("parent_job_id")]
            parents = [
                row
                for row in rows
                if row["graph_node_id"] == "fan" and not row.get("parent_job_id")
            ]
            assert len(shards) == 50
            assert all(row["state"] == "done" for row in shards)
            assert len(parents) == 1 and parents[0]["state"] == "done"
            assert parents[0]["expected_shards"] == 50
            async with (
                app.state.db.read() as reader,
                reader.execute(
                    "SELECT kind, count(*) FROM job_events WHERE job_id IN "
                    "(SELECT job_id FROM jobs WHERE parent_job_id IS NOT NULL) "
                    "AND kind IN ('dispatch_sent', 'node_received', 'subprocess_started') GROUP BY kind"
                ) as cursor,
            ):
                assert dict(await cursor.fetchall()) == {
                    "dispatch_sent": 50,
                    "node_received": 50,
                    "subprocess_started": 50,
                }
            async with (
                app.state.db.read() as reader,
                reader.execute("SELECT count(DISTINCT job_id) FROM job_logs") as cursor,
            ):
                assert await cursor.fetchone() == (50,)

        client.portal.call(run)
    assert len(dispatched) == len(set(dispatched)) == 50
    assert attempts == ["database is locked"]


def test_real_db_lock_in_node_ack_orphans_then_reconciles_fanout(tmp_path, capsys):
    app = create_app(db_path=tmp_path / "disconnect.sqlite")
    with TestClient(app) as client:
        client.portal.call(_stop_library_pollers, app)
        with client.websocket_connect("/ws/node") as node:
            node.send_text(encode("register", {"node_name": "lock-test", "v_min": 1, "v_max": 1}))
            envelope, registered = decode(node.receive_text())
            assert envelope.kind == "register_ok"

            async def seed():
                store = app.state.jobs_store
                await store.create(
                    Job(
                        job_id="parent",
                        workflow_id="workflow",
                        snapshot_id=None,
                        algorithm_name="demo",
                        algorithm_version="0.1.0",
                        node_id=registered.node_id,
                        state=JobState.RUNNING,
                        expected_shards=50,
                    )
                )
                await store.create_many(
                    [
                        Job(
                            job_id=f"shard-{index}",
                            workflow_id="workflow",
                            snapshot_id=None,
                            algorithm_name="demo",
                            algorithm_version="0.1.0",
                            node_id=registered.node_id,
                            parent_job_id="parent",
                            state=JobState.RUNNING if index < 5 else JobState.PENDING,
                        )
                        for index in range(50)
                    ]
                )
                async with app.state.db._writer_conn.execute("PRAGMA busy_timeout=20"):
                    pass

            client.portal.call(seed)
            with closing(
                sqlite3.connect(app.state.db._path, timeout=0, check_same_thread=False)
            ) as competitor:
                competitor.execute("BEGIN IMMEDIATE")
                openings = []

                def release_for_disconnect_cleanup(statement):
                    if statement == "BEGIN IMMEDIATE" or (
                        statement == "SAVEPOINT hlbatch_0" and "BEGIN IMMEDIATE" not in openings
                    ):
                        openings.append(statement)
                        if len(openings) == 2:
                            competitor.rollback()

                client.portal.call(
                    app.state.db._writer_conn.set_trace_callback, release_for_disconnect_cleanup
                )
                try:
                    node.send_text(encode("job_ack", JobAck(job_id="shard-0")))

                    async def wait_offline():
                        for _attempt in range(300):
                            if app.state.registry.get_session(registered.node_id) is None:
                                return
                            await asyncio.sleep(0.01)
                        raise AssertionError(
                            "SQLite failure did not finish the node socket handler"
                        )

                    client.portal.call(wait_offline)
                finally:
                    competitor.rollback()
                    client.portal.call(app.state.db._writer_conn.set_trace_callback, None)
                assert len(openings) >= 2
            assert client.portal.call(app.state.jobs_store.get, "parent").state is JobState.ORPHANED
            captured = capsys.readouterr()
            assert "node socket error" in captured.out + captured.err
            assert "database is locked" in captured.out + captured.err

        with client.websocket_connect("/ws/node") as node:
            node.send_text(
                encode(
                    "register",
                    {
                        "node_name": "lock-test",
                        "v_min": 1,
                        "v_max": 1,
                        "node_id": registered.node_id,
                        "node_token": registered.node_token,
                        "running_jobs": ["shard-0"],
                    },
                )
            )
            envelope, _registered_again = decode(node.receive_text())
            assert envelope.kind == "register_ok"

            async def wait_reconciled():
                for _attempt in range(300):
                    rows = await app.state.jobs_store.list_recent(limit=100)
                    states = {row["job_id"]: row["state"] for row in rows}
                    if states.get("shard-49") == "interrupted":
                        assert states["parent"] == "interrupted"
                        assert states["shard-0"] == "running"
                        assert sum(state == "interrupted" for state in states.values()) == 50
                        return
                    await asyncio.sleep(0.01)
                raise AssertionError("fan-out reconciliation did not finish")

            client.portal.call(wait_reconciled)
            node.send_text(encode("job_done", JobDone(job_id="shard-0", output_handles={})))

            async def wait_done():
                for _attempt in range(300):
                    if (await app.state.jobs_store.get("shard-0")).state is JobState.DONE:
                        return
                    await asyncio.sleep(0.01)
                raise AssertionError("claimed shard did not complete")

            client.portal.call(wait_done)
            node.close()
            client.portal.call(wait_offline)
