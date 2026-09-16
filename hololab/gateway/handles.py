"""Handle book — the gateway's address book of produced files/directories.

A ``handle`` is an opaque UUID that identifies one produced artifact. The
handle book records where it lives (node + path), what kind and tags it has,
and its size. Large bytes never traverse the gateway; consumers ``locate`` a
handle and pull from the producing node.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field

import aiosqlite

from hololab.persistence.db import Database


def new_handle_id() -> str:
    """Mint a fresh UUID v4 handle id."""

    return str(uuid.uuid4())


@dataclass
class Handle:
    """One entry in the address book.

    ``storage`` is the physical form on disk ("dir" or "file"). It is a
    transport implementation detail — graph authors never see it, and edge
    compatibility depends on ``tags`` alone.
    """

    handle_id: str
    node_id: str
    storage: str = "dir"  # "dir" | "file"
    tags: list[str] = field(default_factory=list)
    path: str = ""  # absolute path on the producing node
    size_bytes: int | None = None
    sha256: str | None = None
    job_id: str | None = None
    output_port_name: str | None = None
    created_ts: float = field(default_factory=time.time)
    # Non-null when the user asked us to clean this artifact. We keep the
    # row so run history still shows *what was produced*; the timestamp
    # tells the Artifacts page to render the row in the "deleted" bucket
    # instead of "dead" (file gone but not by us).
    deleted_ts: float | None = None


class HandleBook:
    """Async CRUD on the ``handles`` SQLite table."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def register(self, handle: Handle) -> None:
        """Insert a handle, replacing on conflict (idempotent re-registration)."""

        tags_json = json.dumps(handle.tags)

        async def _write(conn: aiosqlite.Connection) -> None:
            # Persist the tombstone if the caller supplied one (mostly a
            # test / import path — production register() calls arrive
            # from a fresh job with ``deleted_ts=None``). On CONFLICT we
            # unset the tombstone: a re-register means the artifact is
            # being produced again, e.g. rerun-from-node overwriting an
            # existing path. The Artifacts-page delete flow doesn't come
            # through here; it calls ``mark_deleted`` directly.
            await conn.execute(
                """
                INSERT INTO handles
                    (handle_id, node_id, job_id, kind, tags_json, path, size_bytes, sha256,
                     output_port_name, created_ts, deleted_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(handle_id) DO UPDATE SET
                    node_id=excluded.node_id,
                    job_id=excluded.job_id,
                    kind=excluded.kind,
                    tags_json=excluded.tags_json,
                    path=excluded.path,
                    size_bytes=excluded.size_bytes,
                    sha256=excluded.sha256,
                    output_port_name=excluded.output_port_name,
                    deleted_ts=NULL
                """,
                (
                    handle.handle_id,
                    handle.node_id,
                    handle.job_id,
                    handle.storage,
                    tags_json,
                    handle.path,
                    handle.size_bytes,
                    handle.sha256,
                    handle.output_port_name,
                    handle.created_ts,
                    handle.deleted_ts,
                ),
            )

        await self._db.write(_write)

    _COLS = (
        "handle_id, node_id, job_id, kind, tags_json, path, size_bytes, sha256, "
        "output_port_name, created_ts, deleted_ts"
    )

    @staticmethod
    def _row_to_handle(row: tuple) -> Handle:
        return Handle(
            handle_id=row[0],
            node_id=row[1],
            job_id=row[2],
            storage=_normalize_storage(row[3]),
            tags=json.loads(row[4]),
            path=row[5],
            size_bytes=row[6],
            sha256=row[7],
            output_port_name=row[8],
            created_ts=row[9],
            deleted_ts=row[10],
        )

    async def get(self, handle_id: str) -> Handle | None:
        """Fetch one handle by id, or ``None`` if unknown."""

        async with (
            self._db.read() as conn,
            conn.execute(
                f"SELECT {self._COLS} FROM handles WHERE handle_id=?",
                (handle_id,),
            ) as cur,
        ):
            row = await cur.fetchone()

        if row is None:
            return None
        return self._row_to_handle(row)

    async def list_by_job(self, job_id: str) -> list[Handle]:
        """All handles produced by a given job."""

        async with (
            self._db.read() as conn,
            conn.execute(
                f"SELECT {self._COLS} FROM handles WHERE job_id=? ORDER BY created_ts",
                (job_id,),
            ) as cur,
        ):
            rows = await cur.fetchall()

        return [self._row_to_handle(row) for row in rows]

    async def list_by_snapshot(self, snapshot_id: str) -> list[Handle]:
        """All handles produced by any job that ran in this snapshot.

        Powers the per-run drilldown on the Artifacts page — one snapshot
        = one row on the RunsPanel, expanded into every artifact it left
        behind. Joins on ``jobs`` so we stay in one query.
        """

        async with (
            self._db.read() as conn,
            conn.execute(
                f"""
                SELECT {", ".join("h." + c.strip() for c in self._COLS.split(","))}
                FROM handles h
                JOIN jobs j ON h.job_id = j.job_id
                WHERE j.snapshot_id = ?
                ORDER BY h.created_ts DESC
                """,
                (snapshot_id,),
            ) as cur,
        ):
            rows = await cur.fetchall()

        return [self._row_to_handle(row) for row in rows]

    async def list_by_workflow(self, workflow_id: str) -> list[Handle]:
        """All handles for jobs belonging to a workflow, newest first.

        Backs the Artifacts page's per-workflow view. We JOIN against the
        ``jobs`` table on ``job_id`` so we don't need a redundant
        ``workflow_id`` column on handles. Includes deleted rows —
        callers filter by ``deleted_ts`` when they only want live ones.
        """

        async with (
            self._db.read() as conn,
            conn.execute(
                f"""
                SELECT {", ".join("h." + c.strip() for c in self._COLS.split(","))}
                FROM handles h
                JOIN jobs j ON h.job_id = j.job_id
                WHERE j.workflow_id = ?
                ORDER BY h.created_ts DESC
                """,
                (workflow_id,),
            ) as cur,
        ):
            rows = await cur.fetchall()

        return [self._row_to_handle(row) for row in rows]

    async def list_all(self, *, limit: int | None = None) -> list[Handle]:
        """Every handle in the book, newest first. Backs the "all workflows"
        view in the Artifacts page.
        """

        limit_sql = f" LIMIT {int(limit)}" if limit is not None else ""
        async with (
            self._db.read() as conn,
            conn.execute(
                f"SELECT {self._COLS} FROM handles ORDER BY created_ts DESC{limit_sql}"
            ) as cur,
        ):
            rows = await cur.fetchall()

        return [self._row_to_handle(row) for row in rows]

    async def mark_deleted(self, handle_id: str, *, ts: float | None = None) -> bool:
        """Stamp ``deleted_ts`` on this handle. Idempotent: a second call
        overwrites the timestamp but doesn't error. Returns ``True`` if
        the row existed and was updated.
        """

        ts = ts if ts is not None else time.time()

        async def _write(conn: aiosqlite.Connection) -> None:
            await conn.execute(
                "UPDATE handles SET deleted_ts=? WHERE handle_id=?",
                (ts, handle_id),
            )

        await self._db.write(_write)
        # We could check rowcount, but SQLite's aiosqlite bridge makes
        # that awkward; a follow-up ``get()`` by the caller confirms
        # anyway. Return True optimistically — errors on the write path
        # would have raised.
        return True

    async def count_by_workflow(self, workflow_id: str) -> dict[str, int]:
        """Cheap DB-only rollup for the Runs list: {total, deleted}.

        We deliberately don't touch the filesystem here — that's the
        Artifacts page's job (opt-in FS check). The runs list just needs
        to badge each row with "N artifacts, K user-cleaned" so the user
        can spot dead-looking runs at a glance without paying for a
        stat() round-trip on every gallery hit.
        """

        async with (
            self._db.read() as conn,
            conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN deleted_ts IS NOT NULL THEN 1 ELSE 0 END) AS deleted
                FROM handles h
                JOIN jobs j ON h.job_id = j.job_id
                WHERE j.workflow_id = ?
                """,
                (workflow_id,),
            ) as cur,
        ):
            row = await cur.fetchone()

        if row is None:
            return {"total": 0, "deleted": 0}
        return {"total": int(row[0] or 0), "deleted": int(row[1] or 0)}

    async def count_by_snapshot(self, snapshot_id: str) -> dict[str, int]:
        """Same rollup, scoped to one snapshot (one row in the Runs list)."""

        async with (
            self._db.read() as conn,
            conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN deleted_ts IS NOT NULL THEN 1 ELSE 0 END) AS deleted
                FROM handles h
                JOIN jobs j ON h.job_id = j.job_id
                WHERE j.snapshot_id = ?
                """,
                (snapshot_id,),
            ) as cur,
        ):
            row = await cur.fetchone()

        if row is None:
            return {"total": 0, "deleted": 0}
        return {"total": int(row[0] or 0), "deleted": int(row[1] or 0)}


def _normalize_storage(raw: str | None) -> str:
    """Map legacy PathDir/PathFile values to the current dir/file vocabulary.

    Rows written before the schema was narrowed can carry the old strings.
    Rather than adding a rename migration we translate on read — the DB
    column keeps its ``kind`` name; the Python model uses ``storage``.
    """

    if raw in (None, "", "dir", "PathDir"):
        return "dir"
    if raw in ("file", "PathFile"):
        return "file"
    return raw  # unknown — pass through so a debug session can see it
