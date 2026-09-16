"""Handle book — register / get / list_by_job."""

from __future__ import annotations

from pathlib import Path

import pytest

from hololab.gateway.handles import Handle, HandleBook, new_handle_id
from hololab.persistence.db import open_database


@pytest.fixture
async def db_and_book(tmp_path: Path):
    db = await open_database(tmp_path / "test.sqlite")
    try:
        yield db, HandleBook(db)
    finally:
        await db.close()


async def test_register_and_get(db_and_book) -> None:
    db, book = db_and_book
    hid = new_handle_id()
    await book.register(
        Handle(
            handle_id=hid,
            node_id="n1",
            storage="dir",
            tags=["colmap"],
            path="/tmp/x",
            size_bytes=1024,
            job_id="j1",
        )
    )
    got = await book.get(hid)
    assert got is not None
    assert got.handle_id == hid
    assert got.tags == ["colmap"]
    assert got.size_bytes == 1024
    _ = db


async def test_register_is_idempotent(db_and_book) -> None:
    _, book = db_and_book
    hid = new_handle_id()
    h = Handle(handle_id=hid, node_id="n1", storage="dir", path="/a")
    await book.register(h)
    # Register again with a different path — upsert takes the new one.
    h2 = Handle(handle_id=hid, node_id="n1", storage="dir", path="/b")
    await book.register(h2)
    got = await book.get(hid)
    assert got is not None
    assert got.path == "/b"


async def test_get_missing_returns_none(db_and_book) -> None:
    _, book = db_and_book
    assert await book.get("nonexistent") is None


async def test_list_by_job(db_and_book) -> None:
    _, book = db_and_book
    await book.register(Handle(handle_id="h1", node_id="n", storage="dir", path="/a", job_id="jX"))
    await book.register(Handle(handle_id="h2", node_id="n", storage="dir", path="/b", job_id="jX"))
    await book.register(Handle(handle_id="h3", node_id="n", storage="dir", path="/c", job_id="jY"))
    listed = await book.list_by_job("jX")
    assert {h.handle_id for h in listed} == {"h1", "h2"}
