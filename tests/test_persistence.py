"""Persistence — migrations apply cleanly on a fresh DB and are idempotent."""

from __future__ import annotations

from pathlib import Path

from hololab.persistence.db import open_database
from hololab.persistence.migrations import MIGRATIONS, current_schema_version


async def test_migrations_apply_on_fresh_db(tmp_path: Path) -> None:
    db = await open_database(tmp_path / "fresh.sqlite")
    try:
        async with db.read() as conn:
            v = await current_schema_version(conn)
        assert v == MIGRATIONS[-1][0]
    finally:
        await db.close()


async def test_migrations_are_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "twice.sqlite"

    db = await open_database(path)
    async with db.read() as conn:
        v1 = await current_schema_version(conn)
    await db.close()

    db = await open_database(path)
    async with db.read() as conn:
        v2 = await current_schema_version(conn)
    await db.close()

    assert v1 == v2 == MIGRATIONS[-1][0]


async def test_writer_queue_serializes(tmp_path: Path) -> None:
    """Interleaved writes complete without deadlock and reflect final state."""

    db = await open_database(tmp_path / "wq.sqlite")
    try:

        async def _insert_node(conn, node_id: str) -> None:
            await conn.execute(
                "INSERT INTO nodes (node_id, node_name, created_ts, online) VALUES (?, ?, 0, 0)",
                (node_id, "n"),
            )

        # Fire a dozen writes concurrently. The queue serializes them; result
        # is deterministic row count.
        import asyncio

        await asyncio.gather(
            *[db.write(lambda c, i=i: _insert_node(c, f"n-{i}")) for i in range(12)]
        )

        async with db.read() as conn, conn.execute("SELECT COUNT(*) FROM nodes") as cur:
            (count,) = await cur.fetchone()
        assert count == 12
    finally:
        await db.close()
