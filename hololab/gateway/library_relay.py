"""NAT fallback: the source node uploads outwards; the target waits for staging."""

from __future__ import annotations

import time
import uuid
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from hololab.gateway.library import CHUNK_SIZE, SHA256, service
from hololab.gateway.library_distribution import signal_work
from hololab.gateway.library_transfers import authenticate, check_lease, task_row

router = APIRouter(prefix="/api/library", tags=["library-relay"])


class Fallback(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


async def release_dependents(conn, task):
    await conn.execute(
        "UPDATE library_transfers SET state='pending',source_ref=?,lease_token=NULL,lease_until=NULL,next_retry_ts=NULL,last_error=NULL WHERE relay_transfer_id=? AND state='waiting_source'",
        (task["source_ref"], task["transfer_id"]),
    )
    await conn.execute(
        "UPDATE library_placements SET observed_state='pending',last_error=NULL WHERE (resource_id,version,node_id) IN (SELECT resource_id,version,target_node_id FROM library_transfers WHERE relay_transfer_id=? AND state='pending')",
        (task["transfer_id"],),
    )


async def fail_dependents(conn, transfer_id, error):
    await conn.execute(
        "UPDATE library_transfers SET state='failed',last_error=? WHERE relay_transfer_id=? AND state='waiting_source'",
        (f"Relay failed: {error}", transfer_id),
    )
    await conn.execute(
        "UPDATE library_placements SET observed_state='failed',last_error=? WHERE (resource_id,version,node_id) IN (SELECT resource_id,version,target_node_id FROM library_transfers WHERE relay_transfer_id=? AND state='failed')",
        (f"Relay failed: {error}", transfer_id),
    )


@router.post("/nodes/{node_id}/transfers/{transfer_id}/relay")
async def fallback(
    node_id: str,
    transfer_id: str,
    body: Fallback,
    request: Request,
    x_transfer_lease: Annotated[str, Header()],
    authorization: Annotated[str | None, Header()] = None,
):
    lib = service(request)
    await authenticate(lib, node_id, authorization)
    await lib.initialize()

    async def write(conn):
        task = await task_row(conn, transfer_id)
        # Lost HTTP response: repeating the same fallback request is safe.
        if (
            task["state"] == "waiting_source"
            and task["lease_token"] == x_transfer_lease
            and task["target_node_id"] == node_id
        ):
            return {"state": "waiting_source", "route": "gateway_relay"}
        check_lease(task, node_id, x_transfer_lease)
        if task["route"] != "peer_direct" or not task["source_node_id"]:
            raise HTTPException(409, "Only a failed peer-direct transfer can request relay")
        async with conn.execute(
            "SELECT 1 FROM library_placements WHERE resource_id=? AND version=? AND node_id=? AND desired_state='present' AND observed_state='ready' AND verified_sha256=?",
            (task["resource_id"], task["version"], task["source_node_id"], task["sha256"]),
        ) as cur:
            if not await cur.fetchone():
                raise HTTPException(409, "Selected relay source is no longer verified")
        async with conn.execute(
            "SELECT transfer_id FROM library_transfers WHERE resource_id=? AND version=? AND action='relay_upload' AND target_node_id=? AND state IN ('pending','running','waiting') LIMIT 1",
            (task["resource_id"], task["version"], task["source_node_id"]),
        ) as cur:
            existing = await cur.fetchone()
        if existing:
            relay_id = existing[0]
        else:
            async with conn.execute(
                "SELECT COALESCE(SUM(reserved_bytes),0) FROM library_upload_sessions"
            ) as cur:
                used = (await cur.fetchone())[0]
            needed = task["size_bytes"] * 2
            if (
                task["size_bytes"] > lib.config.max_file_bytes
                or used + needed > lib.config.staging_quota_bytes
            ):
                raise HTTPException(
                    507,
                    f"Relay staging quota exceeded: need {needed} bytes; available {max(0, lib.config.staging_quota_bytes - used)}",
                )
            relay_id, upload_id = str(uuid.uuid4()), str(uuid.uuid4())
            now = time.time()
            await conn.execute(
                "INSERT INTO library_upload_sessions(upload_id,resource_id,version,expected_size,expected_sha256,chunk_size,reserved_bytes,staging_key,created_ts,last_activity_ts,purpose,uploader_node_id) VALUES (?,?,?,?,?,?,?,?,?,?,'relay',?)",
                (
                    upload_id,
                    task["resource_id"],
                    task["version"],
                    task["size_bytes"],
                    task["sha256"],
                    CHUNK_SIZE,
                    needed,
                    upload_id,
                    now,
                    now,
                    task["source_node_id"],
                ),
            )
            await conn.execute(
                "INSERT INTO library_transfers(transfer_id,resource_id,version,target_node_id,source_ref,delete_epoch,action,route,source_node_id) VALUES (?,?,?,?,?,?,'relay_upload','gateway_relay',?)",
                (
                    relay_id,
                    task["resource_id"],
                    task["version"],
                    task["source_node_id"],
                    upload_id,
                    task["delete_epoch"],
                    task["source_node_id"],
                ),
            )
        # Switching transport is part of the same delivery attempt. Waiting for
        # an outbound source upload must neither hold a slot nor exhaust B's budget.
        await conn.execute(
            "UPDATE library_transfers SET state='waiting_source',attempts=MAX(0,attempts-1),route='gateway_relay',relay_transfer_id=?,lease_until=NULL,fallback_reason=?,last_error=NULL WHERE transfer_id=?",
            (relay_id, body.reason, transfer_id),
        )
        await conn.execute(
            "UPDATE library_placements SET observed_state='waiting_source' WHERE resource_id=? AND version=? AND node_id=?",
            (task["resource_id"], task["version"], node_id),
        )
        return {"state": "waiting_source", "route": "gateway_relay"}

    result = await lib.db.write(write)
    signal_work(request.app)
    return result


async def relay_session(request, node_id, transfer_id, lease, authorization):
    lib = service(request)
    await authenticate(lib, node_id, authorization)
    async with lib.db.read() as conn:
        task = await task_row(conn, transfer_id)
    check_lease(task, node_id, lease)
    if task["action"] != "relay_upload":
        raise HTTPException(409, "Not a relay upload task")
    session = await lib.session(task["source_ref"])
    if session["purpose"] != "relay" or session["uploader_node_id"] != node_id:
        raise HTTPException(403, "Relay upload owner mismatch")
    return lib, session


@router.get("/nodes/{node_id}/transfers/{transfer_id}/upload")
async def get_upload(
    node_id: str,
    transfer_id: str,
    request: Request,
    x_transfer_lease: Annotated[str, Header()],
    authorization: Annotated[str | None, Header()] = None,
):
    lib, session = await relay_session(
        request, node_id, transfer_id, x_transfer_lease, authorization
    )
    return await lib.detail(session["upload_id"])


@router.put("/nodes/{node_id}/transfers/{transfer_id}/upload/parts/{index}")
async def put_part(
    node_id: str,
    transfer_id: str,
    index: int,
    request: Request,
    x_transfer_lease: Annotated[str, Header()],
    x_chunk_sha256: Annotated[str, Header(pattern=SHA256)],
    authorization: Annotated[str | None, Header()] = None,
):
    lib, session = await relay_session(
        request, node_id, transfer_id, x_transfer_lease, authorization
    )
    async with lib.lock(session["upload_id"]):
        # Re-check after waiting for another request holding the same session lock.
        await relay_session(request, node_id, transfer_id, x_transfer_lease, authorization)
        try:
            return await lib.part(session["upload_id"], index, x_chunk_sha256.lower(), request)
        except OSError as exc:
            raise HTTPException(507, f"Relay storage error: {exc.strerror}") from exc


@router.post("/nodes/{node_id}/transfers/{transfer_id}/upload/complete")
async def complete(
    node_id: str,
    transfer_id: str,
    request: Request,
    x_transfer_lease: Annotated[str, Header()],
    authorization: Annotated[str | None, Header()] = None,
):
    lib, session = await relay_session(
        request, node_id, transfer_id, x_transfer_lease, authorization
    )
    async with lib.lock(session["upload_id"]):
        await relay_session(request, node_id, transfer_id, x_transfer_lease, authorization)
        try:
            return await lib.complete(session["upload_id"])
        except OSError as exc:
            raise HTTPException(507, f"Relay assembly error: {exc.strerror}") from exc


async def restart_abandoned_upload(conn, lib, task):
    """Manual retry reacquires quota after a failed relay's abandoned parts expired."""
    async with conn.execute(
        "SELECT state,expected_size FROM library_upload_sessions WHERE upload_id=?",
        (task["source_ref"],),
    ) as cur:
        state, size = await cur.fetchone()
    if state == "cancelling":
        raise HTTPException(409, "Abandoned relay cleanup is still in progress; retry shortly")
    if state != "cancelled":
        return
    async with conn.execute(
        "SELECT COALESCE(SUM(reserved_bytes),0) FROM library_upload_sessions"
    ) as cur:
        used = (await cur.fetchone())[0]
    if size > lib.config.max_file_bytes or used + size * 2 > lib.config.staging_quota_bytes:
        raise HTTPException(507, "Not enough staging quota to restart the relay upload")
    await conn.execute(
        "UPDATE library_upload_sessions SET state='uploading',reserved_bytes=?,last_error=NULL,last_activity_ts=? WHERE upload_id=?",
        (size * 2, time.time(), task["source_ref"]),
    )
