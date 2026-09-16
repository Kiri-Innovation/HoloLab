"""Database wrapper — one writer coroutine, concurrent readers.

Callers use :meth:`Database.read` for SELECTs and :meth:`Database.write` for
mutations. Write is a coroutine call that funnels work through a single
serialized queue; this eliminates writer-writer contention on WAL SQLite and
keeps the model dead simple.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, TypeVar

import aiosqlite

from hololab.persistence.migrations import apply_migrations

T = TypeVar("T")


class Database:
    """One SQLite database, WAL mode, single-writer queue."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._writer_conn: aiosqlite.Connection | None = None
        self._reader_conn: aiosqlite.Connection | None = None
        self._writer_task: asyncio.Task[None] | None = None
        # (fn, future). fn is a callable taking the writer connection.
        self._writer_queue: asyncio.Queue[
            tuple[
                Callable[[aiosqlite.Connection], Awaitable[Any]],
                asyncio.Future[Any],
            ]
        ] = asyncio.Queue()
        self._closed = False

    async def open(self) -> None:
        """Open connections, enable WAL, and apply migrations."""

        self._path.parent.mkdir(parents=True, exist_ok=True)

        self._writer_conn = await aiosqlite.connect(self._path)
        self._reader_conn = await aiosqlite.connect(self._path)

        # WAL + foreign keys on both connections.
        for conn in (self._writer_conn, self._reader_conn):
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA foreign_keys=ON")
            await conn.execute("PRAGMA synchronous=NORMAL")

        await apply_migrations(self._writer_conn)

        # Start the serialized writer loop.
        self._writer_task = asyncio.create_task(self._writer_loop(), name="hololab-db-writer")

    async def close(self) -> None:
        """Drain the writer queue and close both connections."""

        if self._closed:
            return
        self._closed = True

        if self._writer_task is not None:
            await self._writer_queue.put(
                (self._sentinel_stop, asyncio.get_event_loop().create_future())
            )
            await self._writer_task

        if self._reader_conn is not None:
            await self._reader_conn.close()
        if self._writer_conn is not None:
            await self._writer_conn.close()

    @staticmethod
    async def _sentinel_stop(_conn: aiosqlite.Connection) -> None:  # pragma: no cover
        # Sentinel for the writer loop; the loop breaks out before calling this.
        return None

    async def _writer_loop(self) -> None:
        """Drain queued writes serially against the writer connection."""

        assert self._writer_conn is not None
        while True:
            fn, fut = await self._writer_queue.get()
            if fn is self._sentinel_stop:
                fut.set_result(None)
                break
            try:
                result = await fn(self._writer_conn)
                await self._writer_conn.commit()
                if not fut.done():
                    fut.set_result(result)
            except Exception as exc:
                # Roll back on error; caller sees the exception.
                with contextlib.suppress(Exception):
                    await self._writer_conn.rollback()
                if not fut.done():
                    fut.set_exception(exc)

    async def write(self, fn: Callable[[aiosqlite.Connection], Awaitable[T]]) -> T:
        """Enqueue a write and await its result.

        The callable receives an ``aiosqlite.Connection`` bound to the writer.
        Commit and rollback are handled by the loop; ``fn`` should NOT call
        commit itself.
        """

        if self._closed:
            raise RuntimeError("database is closed")
        fut: asyncio.Future[T] = asyncio.get_event_loop().create_future()
        await self._writer_queue.put((fn, fut))
        return await fut

    @asynccontextmanager
    async def read(self):
        """Yield the reader connection for a SELECT.

        Multiple readers are safe because SQLite in WAL mode allows concurrent
        reads with the ongoing writer.
        """

        if self._reader_conn is None:
            raise RuntimeError("database is not open")
        yield self._reader_conn


async def open_database(path: Path) -> Database:
    """Convenience: construct and open a :class:`Database`."""

    db = Database(path)
    await db.open()
    return db
