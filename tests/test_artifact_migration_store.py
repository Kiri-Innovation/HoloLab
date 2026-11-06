import sqlite3

import pytest
import pytest_asyncio

from hololab.artifact_migration_store import MigrationStore
from hololab.persistence.db import Database


@pytest_asyncio.fixture
async def store(tmp_path):
    db = Database(tmp_path / "db")
    await db.open()

    async def seed(c):
        await c.execute("INSERT INTO nodes(node_id,node_name,created_ts) VALUES ('n','test',0)")
        await c.execute(
            "INSERT INTO handles(handle_id,node_id,kind,tags_json,path,created_ts) VALUES ('dir','n','dir','[]',?,0)",
            (str(tmp_path / "source"),),
        )
        await c.execute(
            "INSERT INTO handles(handle_id,node_id,kind,tags_json,path,created_ts) VALUES ('shard','n','file','[]',?,0)",
            (str(tmp_path / "source" / "one"),),
        )

    (tmp_path / "source").mkdir()
    (tmp_path / "source" / "one").write_text("data")
    await db.write(seed)
    yield MigrationStore(db), tmp_path
    await db.close()


async def test_overlap_freeze_cas_and_stale_register(store):
    s, p = store
    plan = await s.plan(
        node_id="n", source=str(p / "source" / "one"), target=str(p / "dest"), handle_id="shard"
    )
    assert len(plan["plan"]["handles"]) == 2
    assert len(plan["units"]) == 1
    await s.claim(plan["migration_id"], plan["plan_digest"], "k")

    async def remove(c):
        await c.execute("DELETE FROM handles WHERE handle_id='shard'")

    with pytest.raises(sqlite3.IntegrityError, match="freeze"):
        await s.db.write(remove)
    await s.state(plan["migration_id"], "prepared")
    await s.commit(plan["migration_id"])
    rows = await s.rows("SELECT path,location_version FROM handles")
    assert all(r["path"].startswith(str(p / "dest")) and r["location_version"] == 1 for r in rows)
    await s.commit(plan["migration_id"])

    async def stale(c):
        await c.execute("UPDATE handles SET path='old' WHERE handle_id='shard'")

    with pytest.raises(sqlite3.IntegrityError):
        await s.db.write(stale)


async def test_commit_rolls_back_all_handles(store):
    s, p = store
    plan = await s.plan(node_id="n", source=str(p / "source"), target=str(p / "dest"))

    await s.claim(plan["migration_id"], plan["plan_digest"], "k")

    async def change(c):
        await c.execute(
            "UPDATE artifact_migration_handles SET expected_version=1 WHERE handle_id='shard'"
        )

    await s.db.write(change)
    await s.state(plan["migration_id"], "prepared")
    with pytest.raises(ValueError, match="changed"):
        await s.commit(plan["migration_id"])
    assert all(
        r["path"].startswith(str(p / "source")) for r in await s.rows("SELECT path FROM handles")
    )


async def test_new_handle_invalidates_plan(store):
    s, p = store
    plan = await s.plan(node_id="n", source=str(p / "source"), target=str(p / "dest"))

    async def extra(c):
        await c.execute(
            "INSERT INTO handles(handle_id,node_id,kind,tags_json,path,created_ts) VALUES ('extra','n','file','[]',?,0)",
            (str(p / "source" / "new"),),
        )

    await s.db.write(extra)
    with pytest.raises(ValueError, match="membership"):
        await s.claim(plan["migration_id"], plan["plan_digest"], "key")
    assert not await s.rows("SELECT * FROM artifact_migration_claims")


async def test_location_version_register_roundtrip_rejects_late_updates(store):
    from hololab.gateway.handles import Handle, HandleBook

    s, p = store
    book = HandleBook(s.db)
    h = await book.get("shard")
    h.path = str(p / "other")
    await book.register(h)
    updated = await book.get("shard")
    assert updated.location_version == 1
    with pytest.raises(ValueError, match="Stale"):
        await book.register(Handle(handle_id="shard", node_id="n", storage="file", path="stale"))
