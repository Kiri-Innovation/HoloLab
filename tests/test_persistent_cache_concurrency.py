"""Best-effort cache access must not compete with gateway job transactions."""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing

import pytest

from hololab.gateway.jobs import Job
from hololab.gateway.registry import JobsStore
from hololab.persistence.db import open_database
from hololab.persistent_cache import PersistentCache


def test_cache_hit_is_read_only_under_wal_writer(tmp_path):
    path = tmp_path / "gateway.sqlite"
    cache = PersistentCache(path)
    cache.put("types", "root", "fingerprint", {"type": "image"})
    with closing(sqlite3.connect(path)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("BEGIN IMMEDIATE")
        try:
            started = time.monotonic()
            assert cache.get("types", "root", "fingerprint") == {"type": "image"}
            assert cache.get_any("types", "root") == {"type": "image"}
            assert time.monotonic() - started < 0.25
        finally:
            writer.rollback()


def test_cache_write_is_nonblocking_and_optional(tmp_path):
    path = tmp_path / "gateway.sqlite"
    cache = PersistentCache(path)
    cache.put("types", "root", "old", {"type": "old"})
    with closing(sqlite3.connect(path)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("BEGIN IMMEDIATE")
        try:
            started = time.monotonic()
            cache.put("types", "root", "new", {"type": "new"})
            assert time.monotonic() - started < 0.25
        finally:
            writer.rollback()
    assert cache.get("types", "root", "old") == {"type": "old"}
    cache.put("types", "root", "new", {"type": "new"})
    assert cache.get("types", "root", "new") == {"type": "new"}
    assert cache.get("types", "root", "old") is None


@pytest.mark.asyncio
async def test_cache_hit_cannot_invalidate_gateway_read_then_write(tmp_path):
    database = await open_database(tmp_path / "gateway.sqlite")
    cache = PersistentCache(database._path)
    cache.put("types", "root", "fingerprint", {"type": "image"})
    try:
        await JobsStore(database).create(
            Job(
                job_id="probe",
                workflow_id="workflow",
                snapshot_id=None,
                algorithm_name="demo",
                algorithm_version="0.1.0",
            )
        )

        async def write_after_cache_read(conn):
            async with conn.execute("SELECT count(*) FROM job_events") as cursor:
                assert await cursor.fetchone() == (1,)
            assert cache.get("types", "root", "fingerprint") == {"type": "image"}
            await conn.execute(
                "INSERT INTO job_events(job_id, kind, payload_json, ts) "
                "VALUES ('probe', 'cache-interleave', '{}', 0)"
            )

        await database.write(write_after_cache_read)
        async with (
            database.read() as reader,
            reader.execute(
                "SELECT kind FROM job_events WHERE job_id='probe' AND kind='cache-interleave'"
            ) as cursor,
        ):
            assert await cursor.fetchall() == [("cache-interleave",)]
    finally:
        await database.close()


@pytest.mark.parametrize("operation", ["get", "get_any", "put", "missing", "invalid_json"])
def test_cache_closes_real_connections_on_every_path(tmp_path, monkeypatch, operation):
    path = tmp_path / "cache.sqlite"
    cache = PersistentCache(path)
    cache.put("types", "root", "fingerprint", {"type": "image"})
    if operation == "invalid_json":
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("UPDATE persistent_cache SET payload_json='invalid'")
    connections = []
    real_connect = sqlite3.connect

    class TrackedConnection(sqlite3.Connection):
        closed = False

        def close(self):
            self.closed = True
            super().close()

    def connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs, factory=TrackedConnection)
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    if operation == "get_any":
        assert cache.get_any("types", "root") == {"type": "image"}
    elif operation == "put":
        cache.put("types", "root", "fingerprint", {"type": "new"})
    elif operation == "missing":
        assert cache.get("types", "missing", "fingerprint") is None
    elif operation == "invalid_json":
        assert cache.get("types", "root", "fingerprint") is None
    else:
        assert cache.get("types", "root", "fingerprint") == {"type": "image"}
    assert connections
    assert all(connection.closed for connection in connections)
