"""Durable node-claimed transfers with Gateway staging and optional peer reads."""

from __future__ import annotations

import asyncio
import re
import secrets
import time
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from hololab.gateway.library import CHUNK_SIZE, Library, service
from hololab.gateway.library_distribution import choose_source, signal_work, source_details

LEASE_SECONDS = 120
MAX_ATTEMPTS = 5
router = APIRouter(prefix="/api/library", tags=["library-transfers"])


class TransferCreate(BaseModel):
    resource_id: str
    version: int = Field(default=1, ge=1)
    target_node_id: str


class Progress(BaseModel):
    confirmed_bytes: int = Field(ge=0)


class Receipt(Progress):
    status: Literal["success", "failed"]
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    error: str | None = Field(default=None, max_length=2000)
    error_code: str | None = Field(default=None, max_length=100)
    retryable: bool = False


async def enqueue(conn, resource_id: str, version: int) -> None:
    """Called in the upload-completion transaction; idempotent across retries."""
    async with conn.execute(
        "SELECT node_id,observed_state FROM library_placements WHERE resource_id=? "
        "AND version=? AND desired_state='present'",
        (resource_id, version),
    ) as cur:
        placements = await cur.fetchall()
    for node_id, state in placements:
        if state == "ready":
            continue
        async with conn.execute(
            "SELECT 1 FROM library_transfers WHERE resource_id=? AND version=? AND target_node_id=?",
            (resource_id, version, node_id),
        ) as cur:
            if await cur.fetchone():
                continue
        await conn.execute(
            "INSERT INTO library_transfers(transfer_id,resource_id,version,target_node_id,source_ref,delete_epoch) "
            "SELECT ?,?,?,?,NULL,r.delete_epoch FROM library_resource_versions v "
            "JOIN library_resources r ON r.resource_id=v.resource_id "
            "WHERE v.resource_id=? AND v.version=? AND v.sha256 IS NOT NULL AND r.lifecycle='active' LIMIT 1",
            (str(uuid.uuid4()), resource_id, version, node_id, resource_id, version),
        )


async def authenticate(lib: Library, node_id: str, authorization: str | None) -> None:
    rows = await lib.rows("SELECT node_token FROM nodes WHERE node_id=?", (node_id,))
    token = authorization.removeprefix("Bearer ") if authorization else ""
    if (
        not rows
        or not rows[0]["node_token"]
        or not secrets.compare_digest(token, rows[0]["node_token"])
    ):
        raise HTTPException(401, "Invalid node identity")


async def task_row(conn, transfer_id: str) -> dict:
    async with conn.execute(
        "SELECT t.*,v.size_bytes,v.sha256,s.storage_key,p.relative_path,p.desired_state,"
        "r.lifecycle,r.delete_epoch AS resource_epoch "
        "FROM library_transfers t JOIN library_resource_versions v ON v.resource_id=t.resource_id AND v.version=t.version "
        "LEFT JOIN library_staging_objects s ON s.staging_id=t.source_ref AND s.state='ready' "
        "JOIN library_resources r ON r.resource_id=t.resource_id "
        "JOIN library_placements p ON p.resource_id=t.resource_id AND p.version=t.version "
        "AND p.node_id=t.target_node_id WHERE t.transfer_id=?",
        (transfer_id,),
    ) as cur:
        row = await cur.fetchone()
        if row is None:
            raise HTTPException(404, "Transfer or placement not found")
        return dict(zip([c[0] for c in cur.description], row, strict=True))


def check_lease(task: dict, node_id: str, lease: str, *, duplicate: bool = False) -> None:
    if task["target_node_id"] != node_id or not secrets.compare_digest(
        task["lease_token"] or "", lease
    ):
        raise HTTPException(409, "Transfer lease was superseded")
    if (
        task["lifecycle"] != "active"
        or task["desired_state"] != "present"
        or task["delete_epoch"] != task["resource_epoch"]
    ):
        raise HTTPException(409, "Resource is no longer eligible for transfer")
    if duplicate and task["state"] == "completed":
        return
    if task["state"] != "running" or (task["lease_until"] or 0) <= time.time():
        raise HTTPException(409, "Transfer lease expired; claim again")


def public_task(task: dict) -> dict:
    return {
        key: task[key]
        for key in (
            "transfer_id",
            "resource_id",
            "version",
            "target_node_id",
            "relative_path",
            "size_bytes",
            "sha256",
            "lease_token",
            "lease_until",
            "attempts",
            "delete_epoch",
            "action",
            "route",
            "source_node_id",
            "source_ref",
            "fallback_reason",
        )
    }


@router.post("/transfers", status_code=201)
async def create_transfer(body: TransferCreate, request: Request):
    lib = service(request)

    async def write(conn):
        async with conn.execute(
            "SELECT 1 FROM library_resources r JOIN library_resource_versions v ON r.resource_id=v.resource_id "
            "WHERE r.resource_id=? AND v.version=? AND r.lifecycle='active' AND v.sha256 IS NOT NULL",
            (body.resource_id, body.version),
        ) as cur:
            if not await cur.fetchone():
                raise HTTPException(409, "Resource version has not completed ingestion")
        async with conn.execute(
            "SELECT online FROM nodes WHERE node_id=?", (body.target_node_id,)
        ) as cur:
            row = await cur.fetchone()
            if not row or not row[0]:
                raise HTTPException(409, "Target node is unknown or offline")
        async with conn.execute(
            "SELECT folder_id,name FROM library_resources WHERE resource_id=?", (body.resource_id,)
        ) as cur:
            folder_id, name = await cur.fetchone()
        components = [name]
        while folder_id != "root":
            async with conn.execute(
                "SELECT parent_id,name FROM library_folders WHERE folder_id=?", (folder_id,)
            ) as cur:
                folder_id, name = await cur.fetchone()
                components.append(name)
        await conn.execute(
            "INSERT OR IGNORE INTO library_placements(resource_id,version,node_id,relative_path) VALUES (?,?,?,?)",
            (body.resource_id, body.version, body.target_node_id, "/".join(reversed(components))),
        )
        await enqueue(conn, body.resource_id, body.version)
        async with conn.execute(
            "SELECT transfer_id,state FROM library_transfers WHERE resource_id=? AND version=? AND target_node_id=? ORDER BY rowid DESC LIMIT 1",
            (body.resource_id, body.version, body.target_node_id),
        ) as cur:
            row = await cur.fetchone()
        return {"transfer_id": row[0], "state": row[1]}

    result = await lib.db.write(write)
    signal_work(request.app)
    return result


@router.get("/transfers")
async def list_transfers(request: Request):
    return {
        "transfers": await service(request).rows(
            "SELECT transfer_id,resource_id,version,target_node_id,state,confirmed_bytes,attempts,next_retry_ts,lease_until,last_error,action,route,source_node_id,fallback_reason FROM library_transfers ORDER BY rowid DESC LIMIT 1000"
        )
    }


@router.post("/transfers/{transfer_id}/retry")
async def retry_transfer(transfer_id: str, request: Request):
    lib = service(request)

    async def write(conn):
        task = await task_row(conn, transfer_id)
        if (
            task["state"] not in {"failed", "waiting"}
            or task["lifecycle"] != "active"
            or task["desired_state"] != "present"
        ):
            raise HTTPException(409, "Only failed or waiting active transfers may be retried")
        from hololab.gateway.library_relay import restart_abandoned_upload

        state = "pending"
        if task["action"] == "relay_upload":
            await restart_abandoned_upload(conn, lib, task)
        if task["relay_transfer_id"]:
            upstream = await task_row(conn, task["relay_transfer_id"])
            if upstream["state"] == "failed":
                await restart_abandoned_upload(conn, lib, upstream)
                await conn.execute(
                    "UPDATE library_transfers SET state='pending',attempts=0,next_retry_ts=NULL,lease_token=NULL,lease_until=NULL,last_error=NULL WHERE transfer_id=?",
                    (upstream["transfer_id"],),
                )
            if upstream["state"] != "completed":
                state = "waiting_source"
        await conn.execute(
            "UPDATE library_transfers SET state=?,attempts=0,next_retry_ts=NULL,lease_token=NULL,lease_until=NULL,last_error=NULL WHERE transfer_id=?",
            (state, transfer_id),
        )
        if task["action"] != "relay_upload":
            await conn.execute(
                "UPDATE library_placements SET observed_state=?,last_error=NULL WHERE resource_id=? AND version=? AND node_id=?",
                (state, task["resource_id"], task["version"], task["target_node_id"]),
            )
        return {"transfer_id": transfer_id, "state": state}

    result = await lib.db.write(write)
    signal_work(request.app)
    return result


@router.post("/nodes/{node_id}/transfers/claim")
async def claim(
    node_id: str, request: Request, authorization: Annotated[str | None, Header()] = None
):
    lib = service(request)
    await authenticate(lib, node_id, authorization)
    now = time.time()

    async def write(conn):
        # Exhausted crashed workers must not leave an immortal running placement.
        async with conn.execute(
            "SELECT transfer_id,action FROM library_transfers WHERE action<>'delete' AND target_node_id=? AND state='running' AND lease_until<=? AND attempts>=?",
            (node_id, now, MAX_ATTEMPTS),
        ) as cur:
            expired = await cur.fetchall()
        for transfer_id, action in expired:
            await conn.execute(
                "UPDATE library_transfers SET state='failed',last_error='Lease expired after maximum attempts' WHERE transfer_id=?",
                (transfer_id,),
            )
            if action == "relay_upload":
                from hololab.gateway.library_relay import fail_dependents

                await fail_dependents(conn, transfer_id, "Lease expired after maximum attempts")
                continue
            await conn.execute(
                "UPDATE library_placements SET observed_state='failed',last_error='Lease expired after maximum attempts' WHERE (resource_id,version,node_id) IN (SELECT resource_id,version,target_node_id FROM library_transfers WHERE transfer_id=?)",
                (transfer_id,),
            )
        async with conn.execute(
            "SELECT COUNT(*),COALESCE(SUM(target_node_id=?),0) FROM library_transfers WHERE state='running' AND lease_until>?",
            (node_id, now),
        ) as cur:
            total, per_node = await cur.fetchone()
        if (
            total >= lib.config.max_concurrent_transfers
            or per_node >= lib.config.max_transfers_per_node
        ):
            return {"transfer": None}
        async with conn.execute(
            "SELECT t.transfer_id FROM library_transfers t JOIN library_resources r ON r.resource_id=t.resource_id "
            "JOIN library_placements p ON p.resource_id=t.resource_id AND p.version=t.version AND p.node_id=t.target_node_id "
            "WHERE t.action<>'delete' AND t.target_node_id=? AND t.attempts<? AND r.lifecycle='active' AND p.desired_state='present' "
            "AND (SELECT COUNT(*) FROM library_transfers active WHERE active.resource_id=t.resource_id AND active.version=t.version AND active.state='running' AND active.lease_until>?)<? "
            "AND r.delete_epoch=t.delete_epoch AND ((t.state IN ('pending','waiting') AND COALESCE(t.next_retry_ts,0)<=?) "
            "OR (t.state='running' AND t.lease_until<=?)) ORDER BY t.rowid LIMIT 1",
            (node_id, MAX_ATTEMPTS, now, lib.config.max_transfers_per_resource, now, now),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return {"transfer": None}
        transfer_id = row[0]
        await conn.execute(
            "UPDATE library_transfers SET state='running',attempts=attempts+1,lease_token=?,lease_until=?,last_error=NULL WHERE transfer_id=?",
            (secrets.token_urlsafe(32), now + LEASE_SECONDS, transfer_id),
        )
        task = await task_row(conn, transfer_id)
        if not await choose_source(conn, lib, task):
            state = "failed" if task["attempts"] >= MAX_ATTEMPTS else "waiting"
            await conn.execute(
                "UPDATE library_transfers SET state=?,next_retry_ts=?,lease_until=NULL,last_error='No verified source available' WHERE transfer_id=?",
                (
                    state,
                    now + min(60, 2 ** task["attempts"]) + secrets.randbelow(1000) / 1000,
                    transfer_id,
                ),
            )
            await conn.execute(
                "UPDATE library_placements SET observed_state=?,last_error='No verified source available' WHERE resource_id=? AND version=? AND node_id=?",
                (state, task["resource_id"], task["version"], node_id),
            )
            return {"transfer": None}
        task = await task_row(conn, transfer_id)
        if task["action"] == "relay_upload":
            return {"transfer": public_task(task)}
        await conn.execute(
            "UPDATE library_placements SET observed_state='downloading',last_error=NULL WHERE resource_id=? AND version=? AND node_id=?",
            (task["resource_id"], task["version"], node_id),
        )
        return {"transfer": public_task(task)}

    result = await lib.db.write(write)
    if result["transfer"]:
        result["transfer"].update(await source_details(lib, result["transfer"]))
    return result


@router.post("/nodes/{node_id}/transfers/{transfer_id}/heartbeat")
async def heartbeat(
    node_id: str,
    transfer_id: str,
    body: Progress,
    request: Request,
    x_transfer_lease: Annotated[str, Header()],
    authorization: Annotated[str | None, Header()] = None,
):
    lib = service(request)
    await authenticate(lib, node_id, authorization)

    async def write(conn):
        task = await task_row(conn, transfer_id)
        check_lease(task, node_id, x_transfer_lease)
        if body.confirmed_bytes > task["size_bytes"]:
            raise HTTPException(422, "Progress exceeds file size")
        until = time.time() + LEASE_SECONDS
        await conn.execute(
            "UPDATE library_transfers SET confirmed_bytes=?,lease_until=? WHERE transfer_id=?",
            (body.confirmed_bytes, until, transfer_id),
        )
        return {"lease_until": until, "task": public_task(task)}

    result = await lib.db.write(write)
    result.update(await source_details(lib, result.pop("task")))
    return result


@router.post("/nodes/{node_id}/transfers/{transfer_id}/receipt")
async def receipt(
    node_id: str,
    transfer_id: str,
    body: Receipt,
    request: Request,
    x_transfer_lease: Annotated[str, Header()],
    authorization: Annotated[str | None, Header()] = None,
):
    lib = service(request)
    await authenticate(lib, node_id, authorization)

    async def write(conn):
        task = await task_row(conn, transfer_id)
        check_lease(task, node_id, x_transfer_lease, duplicate=body.status == "success")
        if body.confirmed_bytes > task["size_bytes"]:
            raise HTTPException(422, "Progress exceeds file size")
        if body.status == "success":
            if body.sha256 != task["sha256"] or body.confirmed_bytes != task["size_bytes"]:
                raise HTTPException(422, "Receipt size/SHA-256 does not match resource version")
            if task["action"] == "relay_upload" and not task["storage_key"]:
                raise HTTPException(409, "Relay staging is not complete")
            await conn.execute(
                "UPDATE library_transfers SET state='completed',confirmed_bytes=?,last_error=NULL WHERE transfer_id=?",
                (body.confirmed_bytes, transfer_id),
            )
            if task["action"] == "relay_upload":
                from hololab.gateway.library_relay import release_dependents

                await release_dependents(conn, task)
                return {"state": "completed"}
            await conn.execute(
                "UPDATE library_placements SET observed_state='ready',verified_sha256=?,verified_ts=?,last_error=NULL WHERE resource_id=? AND version=? AND node_id=?",
                (body.sha256, time.time(), task["resource_id"], task["version"], node_id),
            )
            return {"state": "completed"}
        retry = body.retryable and task["attempts"] < MAX_ATTEMPTS
        if body.error_code == "checksum_mismatch" and task["attempts"] >= 2:
            retry = False
        state = "waiting" if retry else "failed"
        delay = min(60, 2 ** task["attempts"]) + secrets.randbelow(1000) / 1000
        error = f"{body.error_code or 'transfer_failed'}: {body.error or 'unknown error'}"
        await conn.execute(
            "UPDATE library_transfers SET state=?,confirmed_bytes=?,next_retry_ts=?,lease_until=NULL,last_error=? WHERE transfer_id=?",
            (
                state,
                body.confirmed_bytes,
                time.time() + delay if retry else None,
                error,
                transfer_id,
            ),
        )
        if task["action"] == "relay_upload":
            if state == "failed":
                from hololab.gateway.library_relay import fail_dependents

                await fail_dependents(conn, transfer_id, error)
            return {"state": state}
        await conn.execute(
            "UPDATE library_placements SET observed_state=?,last_error=? WHERE resource_id=? AND version=? AND node_id=?",
            (state, error, task["resource_id"], task["version"], node_id),
        )
        return {"state": state}

    result = await lib.db.write(write)
    signal_work(request.app)
    return result


@router.get("/nodes/{node_id}/transfers/{transfer_id}/content")
async def content(
    node_id: str,
    transfer_id: str,
    request: Request,
    x_transfer_lease: Annotated[str, Header()],
    authorization: Annotated[str | None, Header()] = None,
):
    lib = service(request)
    await authenticate(lib, node_id, authorization)
    async with lib.db.read() as conn:
        task = await task_row(conn, transfer_id)
    check_lease(task, node_id, x_transfer_lease)
    size = task["size_bytes"]
    etag = f'"{task["sha256"]}"'
    if request.headers.get("if-range", etag) != etag:
        raise HTTPException(412, "Source version changed; restart transfer")
    match = re.fullmatch(r"bytes=(\d+)-(\d+)", request.headers.get("range", ""))
    if not match:
        raise HTTPException(
            416,
            "A bounded single byte Range is required",
            headers={"Content-Range": f"bytes */{size}"},
        )
    start, end = map(int, match.groups())
    if start > end or end >= size or end - start + 1 > CHUNK_SIZE:
        raise HTTPException(
            416,
            "Range outside object or larger than 8 MiB",
            headers={"Content-Range": f"bytes */{size}"},
        )
    if not task["storage_key"]:
        raise HTTPException(503, "No ready Gateway staging object")
    path = lib.root / task["storage_key"]
    if not path.is_file() or path.stat().st_size != size:
        raise HTTPException(503, "Staging payload is unavailable")

    async def stream():
        with path.open("rb") as source:
            source.seek(start)
            remaining = end - start + 1
            while remaining:
                block = await asyncio.to_thread(source.read, min(64 * 1024, remaining))
                if not block:
                    raise OSError("Staging payload truncated during transfer")
                remaining -= len(block)
                yield block

    return StreamingResponse(
        stream(),
        status_code=206,
        media_type="application/octet-stream",
        headers={
            "Content-Length": str(end - start + 1),
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Accept-Ranges": "bytes",
            "ETag": etag,
            "Cache-Control": "no-store",
        },
    )
