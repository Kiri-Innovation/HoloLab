"""Library phase 1: registry and durable fixed-part uploads.

One gateway owns a staging root (OS lock). Per-session locks serialize writes,
completion and cancellation. SQLite's writer queue arbitrates names and quota.
Bytes are fsynced before DB acknowledgement; recovery reconciles durable parts.
No node dispatch or frontend behavior is implemented here.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import hashlib
import os
import re
import shutil
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from hololab.gateway.library_config import LibraryConfig
from hololab.persistence.db import Database

CHUNK_SIZE = 8 * 1024**2
SHA256 = r"^[0-9a-fA-F]{64}$"
Digest = Annotated[str, Field(pattern=SHA256)]


def name_key(name: str) -> str:
    if (
        not name
        or name in {".", ".."}
        or name != name.strip()
        or any(c in name for c in "/\\\x00:")
        or any(ord(c) < 32 for c in name)
        or name.casefold().startswith(".hololab")
        or len(name.encode("utf-8")) > 255
    ):
        raise ValueError(
            "Name must be a single safe filename (no paths or reserved .hololab names)"
        )
    return unicodedata.normalize("NFC", name).casefold()


class UploadCreate(BaseModel):
    filename: str
    size_bytes: int = Field(ge=0)
    sha256: Digest
    folder_id: str = "root"
    target_node_ids: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("filename")
    @classmethod
    def valid_name(cls, value: str) -> str:
        name_key(value)
        return unicodedata.normalize("NFC", value)

    @field_validator("sha256")
    @classmethod
    def lower_digest(cls, value: str) -> str:
        return value.lower()


class FolderCreate(BaseModel):
    name: str
    parent_id: str = "root"

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        name_key(value)
        return unicodedata.normalize("NFC", value)


def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while block := stream.read(1024**2):
            digest.update(block)
            size += len(block)
    return size, digest.hexdigest()


async def disk_work(fn, *args):
    """Do not release a session lock while cancelled thread work still writes."""
    task = asyncio.create_task(asyncio.to_thread(fn, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


class Library:
    def __init__(self, db: Database, config: LibraryConfig):
        self.db = db
        self.config = config
        self.root = config.staging_root.expanduser().resolve()
        self._init_lock = asyncio.Lock()
        self._locks: dict[str, asyncio.Lock] = {}
        self._lock_file = None

    async def rows(self, sql: str, args=()) -> list[dict]:
        async with self.db.read() as conn, conn.execute(sql, args) as cur:
            names = [c[0] for c in cur.description]
            return [dict(zip(names, row, strict=True)) for row in await cur.fetchall()]

    async def execute(self, sql: str, args=()) -> None:
        async def write(conn):
            await conn.execute(sql, args)

        await self.db.write(write)

    def lock(self, upload_id: str) -> asyncio.Lock:
        return self._locks.setdefault(upload_id, asyncio.Lock())

    async def initialize(self) -> None:
        async with self._init_lock:
            if self._lock_file is not None:
                return
            self.root.mkdir(parents=True, exist_ok=True)
            lock_file = (self.root / ".library.lock").open("a")
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                lock_file.close()
                raise HTTPException(
                    503, "Library staging root is already owned by another gateway"
                ) from None
            self._lock_file = lock_file

    async def startup(self) -> None:
        # Existing tests/apps with no library sessions need not touch storage.
        sessions = await self.rows("SELECT * FROM library_upload_sessions")
        if not sessions:
            return
        await self.initialize()
        try:
            for session in sessions:
                async with self.lock(session["upload_id"]):
                    if session["state"] == "cancelling":
                        await self._cancel(session)
                    elif session["state"] == "completed":
                        await disk_work(self._remove_parts, session["upload_id"])
                        await self.execute(
                            "UPDATE library_upload_sessions SET reserved_bytes=expected_size WHERE upload_id=?",
                            (session["upload_id"],),
                        )
                        from hololab.gateway.library_transfers import enqueue

                        async def schedule(conn, row=session):
                            await enqueue(conn, row["resource_id"], row["version"])

                        await self.db.write(schedule)
                    elif session["state"] != "cancelled":
                        await self.reconcile(session)
                        if session["state"] == "completing":
                            await self.execute(
                                "UPDATE library_upload_sessions SET state='uploading' WHERE upload_id=?",
                                (session["upload_id"],),
                            )
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if self._lock_file is not None:
            self._lock_file.close()
            self._lock_file = None

    async def session(self, upload_id: str) -> dict:
        rows = await self.rows(
            "SELECT * FROM library_upload_sessions WHERE upload_id=?", (upload_id,)
        )
        if not rows:
            raise HTTPException(404, "Upload session not found")
        return rows[0]

    def directory(self, session: dict) -> Path:
        return self.root / session["staging_key"]

    async def reconcile(self, session: dict) -> None:
        directory = self.directory(session)

        def scan():
            found = []
            if not directory.exists():
                return found
            for path in directory.iterdir():
                if path.name.endswith(".tmp"):
                    path.unlink()
                    continue
                match = re.fullmatch(r"(\d+)\.([0-9a-f]{64})\.part", path.name)
                if not match:
                    continue
                index, expected_hash = int(match[1]), match[2]
                expected_size = min(CHUNK_SIZE, session["expected_size"] - index * CHUNK_SIZE)
                size, digest = hash_file(path)
                if expected_size <= 0 or size != expected_size or digest != expected_hash:
                    path.unlink()
                    continue
                found.append((session["upload_id"], index, size, digest))
            fsync_dir(directory)
            return found

        found = await disk_work(scan)

        async def write(conn):
            await conn.execute(
                "DELETE FROM library_upload_parts WHERE upload_id=?", (session["upload_id"],)
            )
            await conn.executemany("INSERT INTO library_upload_parts VALUES (?,?,?,?)", found)

        await self.db.write(write)

    async def detail(self, upload_id: str) -> dict:
        row = await self.session(upload_id)
        row.pop("staging_key")
        row["parts"] = await self.rows(
            "SELECT part_index,size_bytes,sha256 FROM library_upload_parts WHERE upload_id=? ORDER BY part_index",
            (upload_id,),
        )
        row["chunk_size"] = CHUNK_SIZE
        objects = await self.rows(
            "SELECT staging_id,state,size_bytes,sha256 FROM library_staging_objects WHERE staging_id=?",
            (upload_id,),
        )
        row["staging_object"] = objects[0] if objects else None
        return row

    async def create(self, body: UploadCreate) -> dict:
        await self.initialize()
        if body.size_bytes > self.config.max_file_bytes:
            raise HTTPException(
                413,
                f"File size {body.size_bytes} exceeds single-file limit {self.config.max_file_bytes} bytes",
            )
        upload_id, resource_id = str(uuid.uuid4()), str(uuid.uuid4())
        now = time.time()
        key = name_key(body.filename)

        async def write(conn):
            async with conn.execute(
                "SELECT COALESCE(SUM(reserved_bytes),0) FROM library_upload_sessions"
            ) as cur:
                used = (await cur.fetchone())[0]
            reserve = body.size_bytes * 2
            if used + reserve > self.config.staging_quota_bytes:
                raise HTTPException(
                    507,
                    f"Staging quota exceeded: need {reserve} bytes (2x file size during assembly), available {max(0, self.config.staging_quota_bytes - used)} bytes",
                )
            async with conn.execute(
                "SELECT 1 FROM library_folders WHERE folder_id=?", (body.folder_id,)
            ) as cur:
                if not await cur.fetchone():
                    raise HTTPException(404, "Library folder not found")
            await self._check_name(conn, body.folder_id, key)
            # Register intent only: phase 1 never sends a command to a node.
            for node_id in set(body.target_node_ids):
                async with conn.execute(
                    "SELECT online FROM nodes WHERE node_id=?", (node_id,)
                ) as cur:
                    node = await cur.fetchone()
                if not node or not node[0]:
                    raise HTTPException(409, f"Target node {node_id} is unknown or offline")
            await conn.execute(
                "INSERT INTO library_resources(resource_id,folder_id,name,normalized_name,created_ts) VALUES (?,?,?,?,?)",
                (resource_id, body.folder_id, body.filename, key, now),
            )
            await conn.execute(
                "INSERT INTO library_resource_versions(resource_id,version,size_bytes,created_ts) VALUES (?,1,?,?)",
                (resource_id, body.size_bytes, now),
            )
            await conn.execute(
                "INSERT INTO library_upload_sessions(upload_id,resource_id,version,expected_size,expected_sha256,chunk_size,reserved_bytes,staging_key,created_ts,last_activity_ts) VALUES (?,?,1,?,?,?,?,?,?,?)",
                (
                    upload_id,
                    resource_id,
                    body.size_bytes,
                    body.sha256,
                    CHUNK_SIZE,
                    reserve,
                    upload_id,
                    now,
                    now,
                ),
            )
            components = [body.filename]
            folder = body.folder_id
            while folder != "root":
                async with conn.execute(
                    "SELECT parent_id,name FROM library_folders WHERE folder_id=?", (folder,)
                ) as cur:
                    folder, name = await cur.fetchone()
                    components.append(name)
            relative_path = "/".join(reversed(components))
            for node_id in set(body.target_node_ids):
                await conn.execute(
                    "INSERT INTO library_placements(resource_id,version,node_id,relative_path) VALUES (?,1,?,?)",
                    (resource_id, node_id, relative_path),
                )

        await self.db.write(write)
        return await self.detail(upload_id)

    @staticmethod
    async def _check_name(conn, folder_id: str, key: str):
        async with conn.execute(
            "SELECT 1 FROM library_resources WHERE folder_id=? AND normalized_name=? AND deleted_ts IS NULL UNION ALL SELECT 1 FROM library_folders WHERE parent_id=? AND normalized_name=?",
            (folder_id, key, folder_id, key),
        ) as cur:
            if await cur.fetchone():
                raise HTTPException(
                    409,
                    "A file or folder with this name already exists; overwriting is not allowed",
                )

    async def folder(self, body: FolderCreate) -> dict:
        folder_id = str(uuid.uuid4())

        async def write(conn):
            async with conn.execute(
                "SELECT 1 FROM library_folders WHERE folder_id=?", (body.parent_id,)
            ) as cur:
                if not await cur.fetchone():
                    raise HTTPException(404, "Parent folder not found")
            await self._check_name(conn, body.parent_id, name_key(body.name))
            await conn.execute(
                "INSERT INTO library_folders VALUES (?,?,?,?,?)",
                (folder_id, body.parent_id, body.name, name_key(body.name), time.time()),
            )

        await self.db.write(write)
        return {"folder_id": folder_id, **body.model_dump()}

    async def part(self, upload_id: str, index: int, digest: str, request: Request) -> dict:
        session = await self.session(upload_id)
        if session["state"] != "uploading":
            raise HTTPException(409, f"Session is {session['state']}; cannot upload parts")
        count = (session["expected_size"] + CHUNK_SIZE - 1) // CHUNK_SIZE
        if not 0 <= index < count:
            raise HTTPException(400, "Part index is outside this file")
        expected = min(CHUNK_SIZE, session["expected_size"] - index * CHUNK_SIZE)
        directory = self.directory(session)
        existing = await self.rows(
            "SELECT * FROM library_upload_parts WHERE upload_id=? AND part_index=?",
            (upload_id, index),
        )
        missing = existing and not (directory / f"{index}.{existing[0]['sha256']}.part").exists()
        if missing or (
            not existing and directory.exists() and any(directory.glob(f"{index}.*.part"))
        ):
            await self.reconcile(session)
            existing = await self.rows(
                "SELECT * FROM library_upload_parts WHERE upload_id=? AND part_index=?",
                (upload_id, index),
            )
        if existing and existing[0]["sha256"] != digest:
            raise HTTPException(409, "Part already exists with different content")
        directory = self.directory(session)
        directory.mkdir(parents=True, exist_ok=True)
        fsync_dir(self.root)
        temp = directory / f"{index}.tmp"
        final = directory / f"{index}.{digest}.part"
        size, hasher = 0, hashlib.sha256()
        try:
            # Verify retries without writing another copy, including after a
            # crash left both the assembled payload and the original parts.
            with contextlib.nullcontext(None) if existing else temp.open("wb") as stream:
                async for block in request.stream():
                    size += len(block)
                    if size > expected:
                        raise HTTPException(413, f"Part must contain exactly {expected} bytes")
                    hasher.update(block)
                    if stream is not None:
                        await disk_work(stream.write, block)
                if size != expected:
                    raise HTTPException(
                        400, f"Part must contain exactly {expected} bytes; received {size}"
                    )
                if hasher.hexdigest() != digest:
                    raise HTTPException(422, "Part SHA-256 mismatch; part discarded")
                if stream is not None:
                    await disk_work(stream.flush)
                    await disk_work(os.fsync, stream.fileno())
            if not existing:
                os.replace(temp, final)
            fsync_dir(directory)

            async def write(conn):
                await conn.execute(
                    "INSERT OR IGNORE INTO library_upload_parts VALUES (?,?,?,?)",
                    (upload_id, index, size, digest),
                )
                await conn.execute(
                    "UPDATE library_upload_sessions SET last_activity_ts=?,last_error=NULL WHERE upload_id=?",
                    (time.time(), upload_id),
                )

            await self.db.write(write)
        finally:
            temp.unlink(missing_ok=True)
        return {"part_index": index, "size_bytes": size, "sha256": digest}

    def _assemble(self, session: dict, parts: list[dict]) -> tuple[int, str]:
        directory = self.directory(session)
        directory.mkdir(parents=True, exist_ok=True)
        fsync_dir(self.root)
        payload = directory / "payload"
        # A crash after rename but before the DB commit is recoverable.
        if payload.exists():
            result = hash_file(payload)
            if result == (session["expected_size"], session["expected_sha256"]):
                return result
            payload.unlink()
        temp = directory / "payload.tmp"
        hasher, size = hashlib.sha256(), 0
        try:
            if len(parts) == 1:
                part = parts[0]
                path = directory / f"{part['part_index']}.{part['sha256']}.part"
                size, digest = hash_file(path)
                if (size, digest) != (session["expected_size"], session["expected_sha256"]):
                    raise HTTPException(422, "File SHA-256 mismatch; no staging object published")
                # Preserve the source until DB commit, without copying bytes.
                os.link(path, temp)
                os.replace(temp, payload)
                fsync_dir(directory)
                return size, digest
            with temp.open("wb") as output:
                for part in parts:
                    path = directory / f"{part['part_index']}.{part['sha256']}.part"
                    with path.open("rb") as source:
                        while block := source.read(1024**2):
                            output.write(block)
                            hasher.update(block)
                            size += len(block)
                output.flush()
                os.fsync(output.fileno())
            if size != session["expected_size"] or hasher.hexdigest() != session["expected_sha256"]:
                raise HTTPException(
                    422,
                    "File SHA-256 or size mismatch; no staging object published. Cancel and create a new session to change the declared digest",
                )
            os.replace(temp, payload)
            fsync_dir(directory)
        finally:
            temp.unlink(missing_ok=True)
        return size, hasher.hexdigest()

    def _remove_parts(self, upload_id: str) -> None:
        directory = self.root / upload_id
        if directory.exists():
            for path in directory.glob("*.part"):
                path.unlink()
            for path in directory.glob("*.tmp"):
                path.unlink()
            fsync_dir(directory)

    async def complete(self, upload_id: str) -> dict:
        session = await self.session(upload_id)
        if session["state"] == "completed":
            await disk_work(self._remove_parts, upload_id)
            await self.execute(
                "UPDATE library_upload_sessions SET reserved_bytes=expected_size WHERE upload_id=?",
                (upload_id,),
            )
            return await self.detail(upload_id)
        if session["state"] not in {"uploading", "completing"}:
            raise HTTPException(409, f"Cannot complete a {session['state']} session")
        parts = await self.rows(
            "SELECT * FROM library_upload_parts WHERE upload_id=? ORDER BY part_index", (upload_id,)
        )
        count = (session["expected_size"] + CHUNK_SIZE - 1) // CHUNK_SIZE
        if [p["part_index"] for p in parts] != list(range(count)):
            raise HTTPException(409, f"Incomplete upload: received {len(parts)} of {count} parts")
        await self.execute(
            "UPDATE library_upload_sessions SET state='completing' WHERE upload_id=?", (upload_id,)
        )
        try:
            size, digest = await disk_work(self._assemble, session, parts)
        except (HTTPException, OSError) as exc:
            await self.execute(
                "UPDATE library_upload_sessions SET state='uploading',last_error=? WHERE upload_id=?",
                (str(exc), upload_id),
            )
            if isinstance(exc, FileNotFoundError):
                await self.reconcile(session)
                raise HTTPException(
                    409, "A stored part is missing; query the session and resend missing parts"
                ) from exc
            raise
        now = time.time()

        async def write(conn):
            await conn.execute(
                "INSERT OR IGNORE INTO library_staging_objects(staging_id,resource_id,version,storage_key,state,size_bytes,sha256,created_ts) VALUES (?,?,?,?,'ready',?,?,?)",
                (
                    upload_id,
                    session["resource_id"],
                    session["version"],
                    f"{upload_id}/payload",
                    size,
                    digest,
                    now,
                ),
            )
            await conn.execute(
                "UPDATE library_resource_versions SET sha256=?,ingest_state='staged' WHERE resource_id=? AND version=?",
                (digest, session["resource_id"], session["version"]),
            )
            from hololab.gateway.library_transfers import enqueue

            await enqueue(conn, session["resource_id"], session["version"])
            # Keep peak reservation until redundant parts have been removed.
            await conn.execute(
                "UPDATE library_upload_sessions SET state='completed',last_activity_ts=?,last_error=NULL WHERE upload_id=?",
                (now, upload_id),
            )

        await self.db.write(write)
        await disk_work(self._remove_parts, upload_id)
        await self.execute(
            "UPDATE library_upload_sessions SET reserved_bytes=expected_size WHERE upload_id=?",
            (upload_id,),
        )
        return await self.detail(upload_id)

    async def _cancel(self, session: dict) -> None:
        directory = self.directory(session)
        if directory.exists():
            await disk_work(shutil.rmtree, directory)
            fsync_dir(self.root)

        async def write(conn):
            await conn.execute(
                "DELETE FROM library_upload_parts WHERE upload_id=?", (session["upload_id"],)
            )
            await conn.execute(
                "UPDATE library_upload_sessions SET state='cancelled',reserved_bytes=0,last_activity_ts=? WHERE upload_id=?",
                (time.time(), session["upload_id"]),
            )
            await conn.execute(
                "UPDATE library_resources SET lifecycle='deleted',deleted_ts=? WHERE resource_id=?",
                (time.time(), session["resource_id"]),
            )
            await conn.execute(
                "UPDATE library_resource_versions SET ingest_state='cancelled' WHERE resource_id=? AND version=?",
                (session["resource_id"], session["version"]),
            )
            await conn.execute(
                "UPDATE library_placements SET desired_state='absent',observed_state='absent' WHERE resource_id=? AND version=?",
                (session["resource_id"], session["version"]),
            )

        await self.db.write(write)

    async def cancel(self, upload_id: str) -> dict:
        session = await self.session(upload_id)
        if session["state"] == "completed":
            raise HTTPException(
                409, "Upload already completed; resource deletion is a separate GC operation"
            )
        if session["state"] != "cancelled":
            await self.execute(
                "UPDATE library_upload_sessions SET state='cancelling' WHERE upload_id=?",
                (upload_id,),
            )
            await self._cancel(session)
        return await self.detail(upload_id)


router = APIRouter(prefix="/api/library", tags=["library"])


def service(request: Request) -> Library:
    return request.app.state.library


@router.post("/uploads", status_code=201)
async def create_upload(body: UploadCreate, request: Request):
    try:
        return await service(request).create(body)
    except OSError as exc:
        raise HTTPException(507, f"Cannot initialize staging storage: {exc.strerror}") from exc


@router.get("/uploads")
async def list_uploads(
    request: Request, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)
):
    return {
        "sessions": await service(request).rows(
            "SELECT upload_id,resource_id,version,expected_size,expected_sha256,chunk_size,state,reserved_bytes,created_ts,last_activity_ts,last_error FROM library_upload_sessions ORDER BY created_ts DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
    }


@router.get("/uploads/{upload_id}")
async def get_upload(upload_id: str, request: Request):
    return await service(request).detail(upload_id)


@router.put("/uploads/{upload_id}/parts/{part_index}")
async def put_part(
    upload_id: str,
    part_index: int,
    request: Request,
    x_chunk_sha256: Annotated[str, Header(pattern=SHA256)],
):
    lib = service(request)
    async with lib.lock(upload_id):
        await lib.initialize()
        try:
            return await lib.part(upload_id, part_index, x_chunk_sha256.lower(), request)
        except OSError as exc:
            raise HTTPException(507, f"Cannot store upload part: {exc.strerror}") from exc


@router.post("/uploads/{upload_id}/complete")
async def complete_upload(upload_id: str, request: Request):
    lib = service(request)
    async with lib.lock(upload_id):
        try:
            return await lib.complete(upload_id)
        except OSError as exc:
            raise HTTPException(507, f"Cannot assemble upload: {exc.strerror}") from exc


@router.delete("/uploads/{upload_id}")
async def cancel_upload(upload_id: str, request: Request):
    lib = service(request)
    async with lib.lock(upload_id):
        try:
            return await lib.cancel(upload_id)
        except OSError as exc:
            raise HTTPException(
                507, f"Cannot clean up upload; cancellation remains pending: {exc.strerror}"
            ) from exc


@router.get("/folders")
async def list_folders(request: Request):
    return {
        "folders": await service(request).rows("SELECT * FROM library_folders ORDER BY created_ts")
    }


@router.post("/folders", status_code=201)
async def create_folder(body: FolderCreate, request: Request):
    return await service(request).folder(body)
