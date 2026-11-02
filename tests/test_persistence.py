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


async def test_artifact_cleanup_migration_preserves_existing_handles(tmp_path, monkeypatch):
    """Upgrade the previous schema without rewriting any artifact identities."""
    from hololab.persistence import migrations

    path = tmp_path / "upgrade.sqlite"
    with monkeypatch.context() as old_schema:
        old_schema.setattr(migrations, "MIGRATIONS", migrations.MIGRATIONS[:-1])
        db = await open_database(path)
        try:

            async def seed(conn):
                await conn.execute(
                    "INSERT INTO handles (handle_id,node_id,kind,tags_json,path,created_ts) "
                    "VALUES (?,?,?,?,?,?)",
                    ("retained-uuid", "node", "dir", '["image"]', "/unchanged/output", 123.0),
                )

            await db.write(seed)
            async with db.read() as conn, conn.execute("SELECT * FROM handles") as cur:
                before = await cur.fetchall()
        finally:
            await db.close()
    db = await open_database(path)
    try:
        async with db.read() as conn:
            async with conn.execute("SELECT * FROM handles") as cur:
                assert await cur.fetchall() == before
            for table in ("artifact_cleanup_claims", "snapshot_cleanup_claims"):
                async with conn.execute(f"SELECT COUNT(*) FROM {table}") as cur:
                    assert (await cur.fetchone())[0] == 0
            assert await current_schema_version(conn) == migrations.MIGRATIONS[-1][0]
    finally:
        await db.close()
