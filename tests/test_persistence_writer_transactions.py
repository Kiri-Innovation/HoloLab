"""Real WAL transactions, independent writers and durable job events."""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
import pytest_asyncio

from hololab.gateway.jobs import Job, JobState, JobStateMachine, event_from_transition
from hololab.gateway.registry import JobsStore
from hololab.persistence.db import Database, open_database


@pytest_asyncio.fixture
async def database(tmp_path: Path):
    database = await open_database(tmp_path / "writer.sqlite")
    try:

        async def create_probe(conn):
            await conn.execute("CREATE TABLE writer_probe (value TEXT PRIMARY KEY)")

        await database.write(create_probe)
        yield database
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_code_identity_reserves_writer_before_reading(database: Database) -> None:
    store = JobsStore(database)
    await store.create(
        Job(
            job_id="shard",
            workflow_id="workflow",
            snapshot_id=None,
            algorithm_name="demo",
            algorithm_version="0.1.0",
            state=JobState.PENDING,
        )
    )
    contention = []

    def compete_after_identity_read(statement):
        if not statement.startswith("INSERT INTO job_code_identities"):
            return
        with closing(sqlite3.connect(database._path, timeout=0)) as competitor:
            try:
                with competitor:
                    competitor.execute("INSERT INTO writer_probe VALUES ('competitor')")
            except sqlite3.OperationalError as error:
                contention.append(str(error))
            else:
                contention.append("committed between SELECT and INSERT")

    await database._writer_conn.set_trace_callback(compete_after_identity_read)
    try:
        await store.set_code_identity("shard", {"sha256": "test-content"})
    finally:
        await database._writer_conn.set_trace_callback(None)

    assert contention == ["database is locked"]
    assert await store.get_code_identity("shard") == {"sha256": "test-content"}
    with closing(sqlite3.connect(database._path, timeout=0)) as competitor, competitor:
        competitor.execute("INSERT INTO writer_probe VALUES ('after commit')")


@pytest.mark.asyncio
async def test_batch_has_no_intermediate_commits(database: Database) -> None:
    observed = []
    statements = []

    async def insert_first(conn):
        await conn.execute("INSERT INTO writer_probe VALUES ('first')")

    async def inspect_and_insert_second(conn):
        async with (
            database.read() as reader,
            reader.execute("SELECT * FROM writer_probe") as cursor,
        ):
            observed.extend(await cursor.fetchall())
        await conn.execute("INSERT INTO writer_probe VALUES ('second')")

    await database._writer_conn.set_trace_callback(statements.append)
    try:
        await asyncio.gather(
            database.write(insert_first), database.write(inspect_and_insert_second)
        )
    finally:
        await database._writer_conn.set_trace_callback(None)

    assert observed == []
    assert statements.count("BEGIN IMMEDIATE") == 1
    assert statements.count("COMMIT") == 1
    assert not database._writer_conn.in_transaction


@pytest.mark.asyncio
async def test_real_commit_failure_rolls_back_entire_batch(database: Database) -> None:
    async def create_deferred_constraint(conn):
        await conn.execute(
            "CREATE TABLE deferred_probe (value TEXT REFERENCES writer_probe(value) "
            "DEFERRABLE INITIALLY DEFERRED)"
        )

    await database.write(create_deferred_constraint)

    async def insert_valid(conn):
        await conn.execute("INSERT INTO writer_probe VALUES ('valid')")

    async def insert_invalid(conn):
        await conn.execute("INSERT INTO deferred_probe VALUES ('missing')")

    results = await asyncio.gather(
        database.write(insert_valid), database.write(insert_invalid), return_exceptions=True
    )
    assert all(isinstance(result, sqlite3.IntegrityError) for result in results), results
    async with database.read() as reader, reader.execute("SELECT * FROM writer_probe") as cursor:
        assert await cursor.fetchall() == []
    assert not database._writer_conn.in_transaction
    await database.write(insert_valid)


@pytest.mark.asyncio
async def test_busy_begin_does_not_run_callback_or_stop_writer(database: Database) -> None:
    callbacks = []

    async def insert(conn):
        callbacks.append("executed")
        await conn.execute("INSERT INTO writer_probe VALUES ('resumed')")

    async with database._writer_conn.execute("PRAGMA busy_timeout=1"):
        pass
    with closing(sqlite3.connect(database._path, timeout=0)) as competitor:
        competitor.execute("BEGIN IMMEDIATE")
        try:
            with pytest.raises(sqlite3.OperationalError, match="database is locked"):
                await asyncio.wait_for(database.write(insert), timeout=2)
        finally:
            competitor.rollback()
    assert callbacks == []
    assert not database._writer_task.done()
    assert not database._writer_conn.in_transaction
    await database.write(insert)
    assert callbacks == ["executed"]


@pytest.mark.asyncio
async def test_job_state_and_event_rollback_before_terminal_notification(
    database: Database,
) -> None:
    store = JobsStore(database)
    job = Job(
        job_id="shard",
        workflow_id="workflow",
        snapshot_id=None,
        algorithm_name="demo",
        algorithm_version="0.1.0",
        state=JobState.RUNNING,
    )
    await store.create(job)

    async def defer_event_failure(conn):
        await conn.execute(
            "CREATE TABLE deferred_event (value TEXT REFERENCES writer_probe(value) "
            "DEFERRABLE INITIALLY DEFERRED)"
        )
        await conn.execute(
            "CREATE TRIGGER fail_terminal_event AFTER INSERT ON job_events "
            "WHEN NEW.kind = 'transition:done' BEGIN "
            "INSERT INTO deferred_event VALUES ('missing'); END"
        )

    await database.write(defer_event_failure)
    waiter = asyncio.create_task(store.wait_terminal(job.job_id, timeout_s=3))
    done = JobStateMachine.transition(job, JobState.DONE)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            await store.update(done, "transition:done", "{}")
        assert (await store.get(job.job_id)).state is JobState.RUNNING
        assert not waiter.done()
        async with (
            database.read() as reader,
            reader.execute(
                "SELECT count(*) FROM job_events WHERE kind='transition:done'"
            ) as cursor,
        ):
            assert await cursor.fetchone() == (0,)

        async def remove_failure(conn):
            await conn.execute("DROP TRIGGER fail_terminal_event")

        await database.write(remove_failure)
        kind, payload = event_from_transition(job, done)
        await store.update(done, kind, payload)
        assert (await waiter).state is JobState.DONE
    finally:
        if not waiter.done():
            waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)


@pytest.mark.asyncio
async def test_independent_writer_queues_serialize_read_modify_write(tmp_path: Path) -> None:
    path = tmp_path / "shared.sqlite"
    first = await open_database(path)
    second = await open_database(path)
    try:

        async def seed(conn):
            await conn.execute("CREATE TABLE counter (value INTEGER NOT NULL)")
            await conn.execute("INSERT INTO counter VALUES (0)")

        await first.write(seed)

        async def increment(conn):
            async with conn.execute("SELECT value FROM counter") as cursor:
                (value,) = await cursor.fetchone()
            await asyncio.sleep(0)
            await conn.execute("UPDATE counter SET value=?", (value + 1,))

        results = await asyncio.wait_for(
            asyncio.gather(
                *(
                    database.write(increment)
                    for _attempt in range(25)
                    for database in (first, second)
                ),
                return_exceptions=True,
            ),
            timeout=10,
        )
        assert results == [None] * 50
        async with first.read() as reader:
            async with reader.execute("PRAGMA journal_mode") as cursor:
                assert await cursor.fetchone() == ("wal",)
            async with reader.execute("SELECT value FROM counter") as cursor:
                assert await cursor.fetchone() == (50,)
    finally:
        await asyncio.gather(first.close(), second.close())
