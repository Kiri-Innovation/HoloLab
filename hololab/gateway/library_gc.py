"""Epoch-fenced, node-acknowledged deletion and durable staging reclamation."""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import shutil
import time
import uuid
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request

from hololab.gateway.library import disk_work, fsync_dir, service
from hololab.gateway.library_distribution import resource_status, signal_work
from hololab.gateway.library_transfers import (
    LEASE_SECONDS,
    MAX_ATTEMPTS,
    Receipt,
    authenticate,
    public_task,
    task_row,
)
from hololab.logging import get_logger

router = APIRouter(prefix="/api/library", tags=["library-gc"])
log = get_logger("gateway.library_gc")


def signal_gc(app):
    event = getattr(app.state, "library_gc_event", None)
    if event is not None:
        event.set()


@router.delete("/resources/{resource_id}", status_code=202)
async def delete_resource(resource_id: str, request: Request):
    lib = service(request)

    async def write(conn):
        async with conn.execute(
            "SELECT lifecycle,delete_epoch FROM library_resources WHERE resource_id=?",
            (resource_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            raise HTTPException(404, "Library resource not found")
        lifecycle, epoch = row
        if lifecycle == "deleting":
            # Explicit retry only requeues failed cleanup; successful nodes stay absent.
            await conn.execute(
                "UPDATE library_transfers SET state='pending',attempts=0,next_retry_ts=NULL,lease_until=NULL,lease_token=NULL,last_error=NULL WHERE resource_id=? AND action='delete' AND state='failed'",
                (resource_id,),
            )
            return
        epoch += 1
        await conn.execute(
            "UPDATE library_resources SET lifecycle='deleting',delete_epoch=?,delete_requested_ts=?,deleted_ts=NULL WHERE resource_id=?",
            (epoch, time.time(), resource_id),
        )
        await conn.execute(
            "UPDATE library_transfers SET state='cancelled',lease_until=NULL WHERE resource_id=? AND state IN ('pending','running','waiting','waiting_source')",
            (resource_id,),
        )
        # Even unacknowledged downloads may have published before a lost receipt.
        async with conn.execute(
            "SELECT version,node_id FROM library_placements WHERE resource_id=?", (resource_id,)
        ) as cur:
            placements = await cur.fetchall()
        for version, node_id in placements:
            await conn.execute(
                "INSERT INTO library_transfers(transfer_id,resource_id,version,target_node_id,delete_epoch,action,route) VALUES (?,?,?,?,?,'delete','local_cleanup')",
                (str(uuid.uuid4()), resource_id, version, node_id, epoch),
            )
        await conn.execute(
            "UPDATE library_placements SET desired_state='absent',observed_state='deleting',last_error=NULL WHERE resource_id=?",
            (resource_id,),
        )

    await lib.db.write(write)
    try:
        result = await resource_status(lib, resource_id)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        # An already awake node/collector can finish before this response is read.
        result = {"resource_id": resource_id, "status": "deleted", "remaining_nodes": 0}
    signal_work(request.app)
    signal_gc(request.app)
    return result


def check_delete(task, node_id, lease, *, duplicate=False):
    if (
        task["action"] != "delete"
        or task["target_node_id"] != node_id
        or not secrets.compare_digest(task["lease_token"] or "", lease)
        or task["lifecycle"] != "deleting"
        or task["delete_epoch"] != task["resource_epoch"]
        or task["desired_state"] != "absent"
    ):
        raise HTTPException(409, "Cleanup identity or deletion epoch changed")
    if duplicate and task["state"] == "completed":
        return
    if task["state"] != "running" or (task["lease_until"] or 0) <= time.time():
        raise HTTPException(409, "Cleanup lease expired; claim again")


@router.post("/nodes/{node_id}/deletions/claim")
async def claim_delete(
    node_id: str, request: Request, authorization: Annotated[str | None, Header()] = None
):
    lib = service(request)
    await authenticate(lib, node_id, authorization)
    now = time.time()

    async def write(conn):
        await conn.execute(
            "UPDATE library_transfers SET state='failed',last_error='Cleanup lease expired after maximum attempts' WHERE action='delete' AND target_node_id=? AND state='running' AND lease_until<=? AND attempts>=?",
            (node_id, now, MAX_ATTEMPTS),
        )
        async with conn.execute(
            "SELECT COUNT(*) FROM library_transfers WHERE target_node_id=? AND state='running' AND lease_until>?",
            (node_id, now),
        ) as cur:
            if (await cur.fetchone())[0]:
                return {"transfer": None}
        async with conn.execute(
            "SELECT t.transfer_id FROM library_transfers t JOIN library_resources r ON r.resource_id=t.resource_id WHERE t.action='delete' AND t.target_node_id=? AND r.lifecycle='deleting' AND r.delete_epoch=t.delete_epoch AND t.attempts<? AND ((t.state IN ('pending','waiting') AND COALESCE(t.next_retry_ts,0)<=?) OR (t.state='running' AND t.lease_until<=?)) ORDER BY t.rowid LIMIT 1",
            (node_id, MAX_ATTEMPTS, now, now),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return {"transfer": None}
        await conn.execute(
            "UPDATE library_transfers SET state='running',attempts=attempts+1,lease_token=?,lease_until=?,last_error=NULL WHERE transfer_id=?",
            (secrets.token_urlsafe(32), now + LEASE_SECONDS, row[0]),
        )
        task = await task_row(conn, row[0])
        async with conn.execute(
            "SELECT transfer_id FROM library_transfers WHERE resource_id=? AND version=? AND target_node_id=? AND action='download'",
            (task["resource_id"], task["version"], node_id),
        ) as cur:
            ids = [r[0] for r in await cur.fetchall()]
        return {"transfer": {**public_task(task), "download_ids": ids}}

    return await lib.db.write(write)


@router.post("/nodes/{node_id}/deletions/{transfer_id}/heartbeat")
async def heartbeat_delete(
    node_id: str,
    transfer_id: str,
    request: Request,
    x_transfer_lease: Annotated[str, Header()],
    authorization: Annotated[str | None, Header()] = None,
):
    lib = service(request)
    await authenticate(lib, node_id, authorization)

    async def write(conn):
        task = await task_row(conn, transfer_id)
        check_delete(task, node_id, x_transfer_lease)
        await conn.execute(
            "UPDATE library_transfers SET lease_until=? WHERE transfer_id=?",
            (time.time() + LEASE_SECONDS, transfer_id),
        )

    await lib.db.write(write)
    return {"renewed": True}


@router.post("/nodes/{node_id}/deletions/{transfer_id}/receipt")
async def receipt_delete(
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
        check_delete(task, node_id, x_transfer_lease, duplicate=body.status == "success")
        state = (
            "completed"
            if body.status == "success"
            else ("waiting" if body.retryable and task["attempts"] < MAX_ATTEMPTS else "failed")
        )
        error = (
            None
            if state == "completed"
            else f"{body.error_code or 'cleanup_failed'}: {body.error or 'unknown error'}"
        )
        retry = (
            time.time() + min(60, 2 ** task["attempts"]) + secrets.randbelow(1000) / 1000
            if state == "waiting"
            else None
        )
        await conn.execute(
            "UPDATE library_transfers SET state=?,next_retry_ts=?,lease_until=NULL,last_error=? WHERE transfer_id=?",
            (state, retry, error, transfer_id),
        )
        await conn.execute(
            "UPDATE library_placements SET observed_state=?,delete_ack_epoch=?,last_error=? WHERE resource_id=? AND version=? AND node_id=?",
            (
                "absent" if state == "completed" else "deleting",
                task["delete_epoch"] if state == "completed" else None,
                error,
                task["resource_id"],
                task["version"],
                node_id,
            ),
        )
        return {"state": state}

    result = await lib.db.write(write)
    signal_gc(request.app)
    return result


async def purge_session(lib, session):
    """Called with upload lock held. Mark first so restart can resume disk cleanup."""
    uid = session["upload_id"]
    if str(uuid.UUID(uid)) != uid or session["staging_key"] != uid:
        raise ValueError("Refusing non-session staging path")
    await lib.execute(
        "UPDATE library_staging_objects SET state='purging' WHERE staging_id=?", (uid,)
    )
    directory = lib.directory(session)
    if directory.is_symlink():
        raise ValueError("Refusing symlink staging directory")
    if directory.exists():
        await disk_work(shutil.rmtree, directory)
        fsync_dir(lib.root)

    async def write(conn):
        await conn.execute("DELETE FROM library_upload_parts WHERE upload_id=?", (uid,))
        await conn.execute(
            "UPDATE library_staging_objects SET state='evicted' WHERE staging_id=?", (uid,)
        )
        await conn.execute(
            "UPDATE library_upload_sessions SET reserved_bytes=0,last_error=NULL,state=CASE WHEN ? THEN 'deleted' ELSE state END WHERE upload_id=?",
            (session.get("lifecycle") == "deleting", uid),
        )

    await lib.db.write(write)


async def collect(lib):
    sessions = await lib.rows("SELECT upload_id FROM library_upload_sessions")
    for item in sessions:
        uid = item["upload_id"]
        async with lib.lock(uid):
            rows = await lib.rows(
                "SELECT u.*,r.lifecycle FROM library_upload_sessions u JOIN library_resources r ON r.resource_id=u.resource_id WHERE u.upload_id=?",
                (uid,),
            )
            if not rows:
                continue
            session = rows[0]
            try:
                if session["lifecycle"] == "deleting":
                    await purge_session(lib, session)
                elif session["state"] == "cancelling":
                    await lib._cancel(session)
                elif (
                    session["state"] in {"uploading", "completing"}
                    and session["last_activity_ts"] < time.time() - lib.config.upload_ttl_seconds
                ):
                    if session["purpose"] == "relay":

                        async def abandon(conn, uid=uid):
                            async with conn.execute(
                                "SELECT 1 FROM library_transfers WHERE source_ref=? AND action='relay_upload' AND state IN ('pending','running','waiting') LIMIT 1",
                                (uid,),
                            ) as cur:
                                if await cur.fetchone():
                                    return False
                            await conn.execute(
                                "UPDATE library_upload_sessions SET state='cancelling' WHERE upload_id=?",
                                (uid,),
                            )
                            return True

                        if await lib.db.write(abandon):
                            await lib._cancel(session)
                    else:
                        await lib.cancel(uid)
                elif session["state"] == "completed" and session["reserved_bytes"]:

                    async def eligible(conn, session=session, uid=uid):
                        async with conn.execute(
                            "SELECT COUNT(*),SUM(observed_state='ready' AND desired_state='present') FROM library_placements WHERE resource_id=? AND version=?",
                            (session["resource_id"], session["version"]),
                        ) as cur:
                            total, ready = await cur.fetchone()
                        async with conn.execute(
                            "SELECT 1 FROM library_transfers WHERE resource_id=? AND version=? AND state IN ('pending','running','waiting','waiting_source') LIMIT 1",
                            (session["resource_id"], session["version"]),
                        ) as cur:
                            busy = await cur.fetchone()
                        if total and total == ready and not busy:
                            await conn.execute(
                                "UPDATE library_staging_objects SET state='purging' WHERE staging_id=?",
                                (uid,),
                            )
                            return True
                        return False

                    interrupted = await lib.rows(
                        "SELECT 1 FROM library_staging_objects WHERE staging_id=? AND state='purging'",
                        (uid,),
                    )
                    if interrupted or await lib.db.write(eligible):
                        await purge_session(lib, session)
            except (OSError, ValueError) as exc:
                await lib.execute(
                    "UPDATE library_upload_sessions SET last_error=? WHERE upload_id=?",
                    (str(exc)[:2000], uid),
                )
                log.warning("Library staging cleanup failed", upload_id=uid, error=str(exc))

    async def finalize(conn):
        async with conn.execute(
            "SELECT resource_id FROM library_resources r WHERE lifecycle='deleting' AND NOT EXISTS (SELECT 1 FROM library_placements p WHERE p.resource_id=r.resource_id AND (p.observed_state<>'absent' OR COALESCE(p.delete_ack_epoch,-1)<>r.delete_epoch)) AND NOT EXISTS (SELECT 1 FROM library_upload_sessions u WHERE u.resource_id=r.resource_id AND (u.state<>'deleted' OR u.reserved_bytes<>0 OR u.last_error IS NOT NULL))"
        ) as cur:
            resources = await cur.fetchall()
        for (rid,) in resources:
            await conn.execute(
                "DELETE FROM library_upload_parts WHERE upload_id IN (SELECT upload_id FROM library_upload_sessions WHERE resource_id=?)",
                (rid,),
            )
            for table in (
                "library_transfers",
                "library_placements",
                "library_staging_objects",
                "library_upload_sessions",
                "library_resource_versions",
                "library_resources",
            ):
                await conn.execute(f"DELETE FROM {table} WHERE resource_id=?", (rid,))

    await lib.db.write(finalize)


async def gc_loop(app):
    while True:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(
                app.state.library_gc_event.wait(),
                timeout=app.state.library.config.gc_interval_seconds,
            )
        app.state.library_gc_event.clear()
        try:
            await collect(app.state.library)
        except Exception as exc:
            log.warning("Library GC will retry", error=str(exc))
