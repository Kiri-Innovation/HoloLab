from __future__ import annotations

import asyncio

from hololab.persistence.db import open_database
from hololab.persistent_cache import PersistentCache


def test_persistent_cache_requires_exact_fingerprint(tmp_path):
    cache = PersistentCache(tmp_path / "node-cache.sqlite")
    cache.put("identity", "pack-root:manifest", "tree-a", {"sha256": "a"})
    assert cache.get("identity", "pack-root:manifest", "tree-a") == {"sha256": "a"}
    # A dependency declaration/code witness change must never borrow a row.
    assert cache.get("identity", "pack-root:manifest", "tree-b") is None


def test_gateway_migration_creates_persistent_cache(tmp_path):
    async def check():
        db = await open_database(tmp_path / "hololab.sqlite")
        try:
            async with (
                db.read() as conn,
                conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='persistent_cache'"
                ) as cur,
            ):
                assert await cur.fetchone() == ("persistent_cache",)
        finally:
            await db.close()

    asyncio.run(check())
