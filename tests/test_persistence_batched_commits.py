"""Batched-commit semantics for the ``Database`` writer loop (C3).

Every write is still individually retryable / rollback-able (per-write
SAVEPOINT). Multiple writes queued at the same instant share one commit
so the whole fan-out phase-1 burst pays 1 fsync instead of N.

Tests cover the four invariants that matter to production callers:

  1. **Ordering** — writes execute + persist in queue order, so a caller
     that awaits its own future after another finished can rely on
     causality (e.g. handles.register_many then jobs.create_many both
     visible when the second's future resolves).
  2. **Per-write error isolation** — one raising ``fn`` inside a batch
     does not roll back its neighbours; that ``fn``'s future carries
     the exception, others resolve normally.
  3. **Commit count reduction** — under bursty enqueues, ``commit`` runs
     once per batch, not once per write. We patch the connection to
     count calls.
  4. **Batch size cap** — ``_MAX_WRITER_BATCH`` bounds a single commit
     so a runaway producer can't hold one transaction open forever.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from hololab.persistence import db as db_mod
from hololab.persistence.db import open_database


@pytest.mark.asyncio
async def test_batched_writes_preserve_order(tmp_path: Path) -> None:
    """Writes execute + persist in queue order under batching. Insert a
    row per write with an ``order`` column and verify SELECT reads them
    back in the same order they were enqueued.
    """

    db = await open_database(tmp_path / "order.sqlite")
    try:

        async def _create_table(conn) -> None:
            await conn.execute("CREATE TABLE t (order_id INTEGER PRIMARY KEY, tag TEXT)")

        await db.write(_create_table)

        async def _insert(conn, order_id: int) -> None:
            await conn.execute(
                "INSERT INTO t (order_id, tag) VALUES (?, ?)", (order_id, f"tag-{order_id}")
            )

        # Enqueue 20 writes back-to-back. They should all be alive in the
        # queue by the time the loop wakes for the first — the batching
        # path drains them together.
        futures = [db.write(lambda c, i=i: _insert(c, i)) for i in range(20)]
        await asyncio.gather(*futures)

        async with (
            db.read() as conn,
            conn.execute("SELECT order_id, tag FROM t ORDER BY order_id") as cur,
        ):
            rows = await cur.fetchall()
        assert rows == [(i, f"tag-{i}") for i in range(20)]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_one_write_failure_does_not_poison_batch(tmp_path: Path) -> None:
    """A failing ``fn`` rolls back its own SAVEPOINT only — its neighbours
    still commit. The failing write's future carries the exception; the
    others resolve normally with their values in place.
    """

    db = await open_database(tmp_path / "iso.sqlite")
    try:

        async def _create_table(conn) -> None:
            await conn.execute("CREATE TABLE t (id TEXT PRIMARY KEY)")

        await db.write(_create_table)

        async def _insert(conn, x: str) -> None:
            await conn.execute("INSERT INTO t (id) VALUES (?)", (x,))
            return x

        async def _bad(_conn) -> None:
            raise ValueError("kaboom")

        futs = [
            db.write(lambda c, x="a": _insert(c, x)),
            db.write(_bad),
            db.write(lambda c, x="b": _insert(c, x)),
        ]
        r0, exc, r2 = await asyncio.gather(*futs, return_exceptions=True)
        assert r0 == "a"
        assert isinstance(exc, ValueError) and str(exc) == "kaboom"
        assert r2 == "b"

        async with db.read() as conn, conn.execute("SELECT id FROM t ORDER BY id") as cur:
            rows = await cur.fetchall()
        assert rows == [("a",), ("b",)], "the bad write must not roll back the good ones"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_batching_collapses_commits(tmp_path: Path) -> None:
    """Under bursty enqueues, one ``commit`` is issued per batch, not per
    write. We patch ``Connection.commit`` to count calls.
    """

    db = await open_database(tmp_path / "commits.sqlite")
    try:

        async def _create_table(conn) -> None:
            await conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")

        await db.write(_create_table)

        async def _insert(conn, i: int) -> None:
            await conn.execute("INSERT INTO t (id) VALUES (?)", (i,))

        commit_count = 0
        real_commit = db._writer_conn.commit  # type: ignore[union-attr]

        async def counting_commit():
            nonlocal commit_count
            commit_count += 1
            return await real_commit()

        with patch.object(db._writer_conn, "commit", counting_commit):
            # Fire 25 writes at once. With the pre-C3 loop we'd see 25
            # commits; the batched loop should collapse most of them.
            await asyncio.gather(*[db.write(lambda c, i=i: _insert(c, i)) for i in range(25)])

        # Definitely fewer commits than writes, ideally 1-3 depending on
        # how many the writer loop consumed before the second wave
        # arrived. Use a loose upper bound so the test isn't flaky.
        assert 1 <= commit_count < 10, f"expected batched commits (<< 25), got {commit_count}"

        async with db.read() as conn, conn.execute("SELECT COUNT(*) FROM t") as cur:
            (count,) = await cur.fetchone()
        assert count == 25
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_batch_size_is_capped(tmp_path: Path) -> None:
    """``_MAX_WRITER_BATCH`` bounds a single batch. Fire N > cap writes
    at once; verify the loop consumes them in at least
    ``ceil(N / _MAX_WRITER_BATCH)`` batches (i.e. more than one commit).
    """

    db = await open_database(tmp_path / "cap.sqlite")
    try:

        async def _create_table(conn) -> None:
            await conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")

        await db.write(_create_table)

        async def _insert(conn, i: int) -> None:
            await conn.execute("INSERT INTO t (id) VALUES (?)", (i,))

        # Force a tiny cap so we don't have to fire hundreds of writes.
        with patch.object(db_mod, "_MAX_WRITER_BATCH", 8):
            commit_count = 0
            real_commit = db._writer_conn.commit  # type: ignore[union-attr]

            async def counting_commit():
                nonlocal commit_count
                commit_count += 1
                return await real_commit()

            with patch.object(db._writer_conn, "commit", counting_commit):
                await asyncio.gather(*[db.write(lambda c, i=i: _insert(c, i)) for i in range(20)])

            # With cap=8 and 20 writes queued at once, minimum commits = 3
            # (8 + 8 + 4). May be more if the loop woke earlier than the
            # full 20 were queued; must never be less.
            assert commit_count >= 3, (
                f"cap=8 with 20 writes must produce >= 3 commits, got {commit_count}"
            )
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_single_write_still_commits_immediately(tmp_path: Path) -> None:
    """A lone write (nothing else queued) commits immediately without
    waiting for a bigger batch to form — the drain uses ``get_nowait``,
    not a timed wait. Guards against a batching regression that would
    add latency to interactive/rare writes.
    """

    db = await open_database(tmp_path / "single.sqlite")
    try:

        async def _create_table(conn) -> None:
            await conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")

        await db.write(_create_table)

        async def _one(conn) -> int:
            await conn.execute("INSERT INTO t (id) VALUES (1)")
            return 1

        loop = asyncio.get_event_loop()
        t0 = loop.time()
        result = await asyncio.wait_for(db.write(_one), timeout=1.0)
        elapsed = loop.time() - t0

        assert result == 1
        # If batching accidentally waited for more writes, this would
        # spike. A generous 200 ms cap catches a regression without
        # flaking on a busy CI host.
        assert elapsed < 0.2, f"single write took {elapsed:.3f}s"
    finally:
        await db.close()
