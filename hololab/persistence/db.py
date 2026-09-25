"""Database wrapper — one writer coroutine, concurrent readers.

Callers use :meth:`Database.read` for SELECTs and :meth:`Database.write` for
mutations. Write is a coroutine call that funnels work through a single
serialized queue; this eliminates writer-writer contention on WAL SQLite and
keeps the model dead simple.

Batched commits (C3): when multiple writes are already queued at the moment
the writer loop wakes up, they are run inside a single transaction and
committed once. Each queued ``fn`` is wrapped in its own SAVEPOINT so a
single bad write does not fail its neighbours; only the whole-transaction
commit at the tail is shared. This trades N fsyncs for 1 in the fan-out
hot path (100-shard image-undistort fan-out issues ~5 writes/shard through
this queue) without changing write ordering or per-``fn`` error semantics.
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

# Cap on writes per commit. High enough to swallow a 100-shard fan-out's
# phase-1 register_many + create_many burst; low enough that a runaway
# writer can't hold a single transaction open indefinitely (each batch is
# still bounded by the queue snapshot at loop wake-up).
_MAX_WRITER_BATCH = 128


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
        """Drain queued writes against the writer connection, batching commits.

        On each loop iteration we block for the next write, then greedily
        drain every write already sitting in the queue (up to
        ``_MAX_WRITER_BATCH``). All drained writes run inside one implicit
        transaction and share a single ``commit`` — replacing N fsyncs with
        1 during bursty phases (e.g. fan-out phase-1). Each write is
        wrapped in its own SAVEPOINT so a single ``fn`` failing rolls back
        just that write's effects; its neighbours proceed normally.

        Ordering is preserved: writes execute in the same order the queue
        yielded them, exactly as before. A caller that awaits its own
        future does not observe out-of-order commits — the batch commit
        completes before any of the batch's futures are fulfilled.
        """

        assert self._writer_conn is not None
        conn = self._writer_conn
        while True:
            first = await self._writer_queue.get()
            batch: list[
                tuple[Callable[[aiosqlite.Connection], Awaitable[Any]], asyncio.Future[Any]]
            ] = [first]
            # Drain everything already queued without waiting. If the
            # queue is empty this loop exits immediately, degrading to
            # the pre-batch case (batch of one).
            while len(batch) < _MAX_WRITER_BATCH:
                try:
                    batch.append(self._writer_queue.get_nowait())
                except asyncio.QueueEmpty:
                    break

            # Split at the first sentinel if present — everything up to
            # it goes through the normal batch commit path; the sentinel
            # itself signals shutdown and anything after it stays queued
            # for the (never-taken) next iteration.
            stop_at: int | None = None
            for i, (fn, _) in enumerate(batch):
                if fn is self._sentinel_stop:
                    stop_at = i
                    break
            work = batch if stop_at is None else batch[:stop_at]

            if work:
                results = await self._execute_batch(conn, work)
                # Fulfill futures in queue order. Any exception raised by
                # ``fn`` was captured per-savepoint; any exception from
                # the batch-wide commit is applied to every entry.
                for (_, fut), (result, exc) in zip(work, results, strict=True):
                    if fut.done():
                        continue
                    if exc is not None:
                        fut.set_exception(exc)
                    else:
                        fut.set_result(result)

            if stop_at is not None:
                _stop_fn, stop_fut = batch[stop_at]
                if not stop_fut.done():
                    stop_fut.set_result(None)
                break

    async def _execute_batch(
        self,
        conn: aiosqlite.Connection,
        work: list[tuple[Callable[[aiosqlite.Connection], Awaitable[Any]], asyncio.Future[Any]]],
    ) -> list[tuple[Any, BaseException | None]]:
        """Run every ``fn`` in ``work`` inside its own SAVEPOINT, then commit
        once. Returns a per-``fn`` list of ``(result, exception_or_None)`` —
        the caller fulfills the futures.

        Per-write savepoints matter because a single write failing (e.g.
        integrity violation) must NOT poison its batch neighbours: we
        ``ROLLBACK TO`` that write's savepoint and continue. If the
        outer ``commit`` itself fails (very rare — disk full, corrupt
        WAL), we roll back everything and every result flips to that
        commit-time exception.
        """

        results: list[tuple[Any, BaseException | None]] = []
        for i, (fn, _fut) in enumerate(work):
            savepoint = f"hlbatch_{i}"
            try:
                await conn.execute(f"SAVEPOINT {savepoint}")
                result = await fn(conn)
                await conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                results.append((result, None))
            except BaseException as exc:
                # Roll back just this write's effects; the batch proceeds.
                # Suppress errors from the rollback itself — if the
                # connection is genuinely broken, the outer commit will
                # surface that.
                with contextlib.suppress(Exception):
                    await conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    await conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                results.append((None, exc))

        # One commit for the whole batch. On commit failure roll back
        # the whole implicit transaction and propagate the error to
        # every future — otherwise a caller would think its write
        # succeeded when it didn't reach disk.
        try:
            await conn.commit()
        except Exception as exc:
            with contextlib.suppress(Exception):
                await conn.rollback()
            return [(None, exc)] * len(work)

        return results

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
