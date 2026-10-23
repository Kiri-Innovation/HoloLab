"""An active read must not hide committed workflow writes from other readers."""

from pathlib import Path

import pytest

from hololab.gateway.workflows import WorkflowGraph, WorkflowStore
from hololab.persistence.db import Database


@pytest.mark.parametrize("operation", ["create", "copy_recipe", "update"])
async def test_workflow_write_reads_back_while_another_cursor_is_unfinished(tmp_path, operation):
    db = Database(Path(tmp_path / "readers.sqlite"))
    await db.open()
    try:
        store = WorkflowStore(db)
        source = await store.save_draft(workflow_id=None, name="source", graph=WorkflowGraph())
        await store.save_draft(workflow_id=None, name="second", graph=WorkflowGraph())
        # Two rows ensure fetchone does NOT exhaust/finalize this SELECT.
        async with db.read() as reader, reader.execute("SELECT workflow_id FROM workflows") as held:
            assert await held.fetchone() is not None
            if operation == "copy_recipe":
                result = await store.copy_recipe(source.workflow_id, "copied")
            else:
                result = await store.save_draft(
                    workflow_id=source.workflow_id if operation == "update" else None,
                    name="saved",
                    graph=source.graph,
                )
            assert result.name == ("copied" if operation == "copy_recipe" else "saved")
            assert await store.get_draft(result.workflow_id) == result
            async with db.read() as fresh:
                assert fresh is not reader
                async with fresh.execute(
                    "SELECT name FROM workflows WHERE workflow_id=?", (result.workflow_id,)
                ) as cur:
                    assert (await cur.fetchone())[0] == result.name
                async with fresh.execute(
                    "SELECT count(*) FROM workflow_bindings WHERE workflow_id=?",
                    (result.workflow_id,),
                ) as cur:
                    assert (await cur.fetchone())[0] == 1
            # Isolation also means the original reader retains its own snapshot.
            async with reader.execute(
                "SELECT name FROM workflows WHERE workflow_id=?", (result.workflow_id,)
            ) as cur:
                old = await cur.fetchone()
                assert old == (("source",) if operation == "update" else None)
    finally:
        await db.close()


async def test_reader_error_does_not_leave_snapshot_or_connection_open(tmp_path):
    db = Database(tmp_path / "cleanup.sqlite")
    await db.open()
    try:
        with pytest.raises(ValueError, match="reader failed"):
            async with db.read() as reader:
                await reader.execute("SELECT * FROM workflows")
                raise ValueError("reader failed")
        with pytest.raises(ValueError, match="no active connection"):
            await reader.execute("SELECT 1")
        store = WorkflowStore(db)
        row = await store.save_draft(workflow_id=None, name="after error", graph=WorkflowGraph())
        assert await store.get_draft(row.workflow_id) == row
    finally:
        await db.close()


async def test_cancel_during_reader_connect_waits_for_cleanup(tmp_path, monkeypatch):
    import asyncio

    import aiosqlite

    db = Database(tmp_path / "cancel-connect.sqlite")
    await db.open()
    try:
        started, release = asyncio.Event(), asyncio.Event()
        connect = aiosqlite.connect
        connections = []

        def slow_connect(*args, **kwargs):
            conn = connect(*args, **kwargs)
            original = conn._connect

            async def delayed():
                started.set()
                await release.wait()
                return await original()

            conn._connect = delayed
            connections.append(conn)
            return conn

        monkeypatch.setattr(aiosqlite, "connect", slow_connect)

        async def read():
            async with db.read():
                pytest.fail("cancelled read must not reach its body")

        task = asyncio.create_task(read())
        await started.wait()
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(ValueError, match="no active connection"):
            await connections[0].execute("SELECT 1")
    finally:
        await db.close()


async def test_shutdown_closes_outstanding_reader_contexts(tmp_path):
    import asyncio

    db = Database(tmp_path / "shutdown.sqlite")
    await db.open()
    entered, release = asyncio.Event(), asyncio.Event()
    readers = []

    async def background_read():
        async with db.read() as conn:
            readers.append(conn)
            entered.set()
            await release.wait()

    task = asyncio.create_task(background_read())
    try:
        await entered.wait()
        await db.close()
        with pytest.raises(ValueError, match="no active connection"):
            await readers[0].execute("SELECT 1")
        assert not db._readers
    finally:
        release.set()
        await task
        await db.close()
