"""Source selection, derived resource status and best-effort WS wakeups."""

from __future__ import annotations

import asyncio
import contextlib
import time

from fastapi import APIRouter, HTTPException, Query, Request

from hololab.gateway.library import service
from hololab.library_transport import capability
from hololab.protocol import encode

router = APIRouter(prefix="/api/library", tags=["library-distribution"])


async def choose_source(conn, lib, task):
    if task["action"] == "relay_upload":
        return True
    async with conn.execute(
        "SELECT s.staging_id,s.storage_key,u.purpose FROM library_staging_objects s "
        "JOIN library_upload_sessions u ON u.upload_id=s.staging_id "
        "WHERE s.resource_id=? AND s.version=? AND s.state='ready'",
        (task["resource_id"], task["version"]),
    ) as cur:
        objects = await cur.fetchall()
    for staging_id, key, purpose in objects:
        path = lib.root / key
        if path.is_file() and path.stat().st_size == task["size_bytes"]:
            route = "gateway_relay" if purpose == "relay" else "gateway_staging"
            await conn.execute(
                "UPDATE library_transfers SET source_ref=?,route=? WHERE transfer_id=?",
                (staging_id, route, task["transfer_id"]),
            )
            return True
    async with conn.execute(
        "SELECT p.node_id FROM library_placements p JOIN nodes n ON n.node_id=p.node_id "
        "WHERE p.resource_id=? AND p.version=? AND p.node_id<>? AND p.observed_state='ready' "
        "AND p.desired_state='present' AND p.verified_sha256=? ORDER BY n.online DESC,p.verified_ts DESC LIMIT 1",
        (task["resource_id"], task["version"], task["target_node_id"], task["sha256"]),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return False
    await conn.execute(
        "UPDATE library_transfers SET source_ref=NULL,source_node_id=?,route='peer_direct' WHERE transfer_id=?",
        (row[0], task["transfer_id"]),
    )
    return True


async def source_details(lib, task):
    if task["route"] != "peer_direct":
        return {}
    rows = await lib.rows(
        "SELECT n.advertised_url,n.node_token,p.relative_path FROM nodes n JOIN library_placements p ON p.node_id=n.node_id WHERE n.node_id=? AND p.resource_id=? AND p.version=?",
        (task["source_node_id"], task["resource_id"], task["version"]),
    )
    if not rows or not rows[0]["advertised_url"] or not rows[0]["node_token"]:
        return {"peer_url": None, "peer_capability": None}
    source = rows[0]
    return {
        "peer_url": source["advertised_url"].rstrip("/") + "/_library/content",
        "peer_capability": capability(
            source["node_token"],
            path=source["relative_path"],
            sha256=task["sha256"],
            size=task["size_bytes"],
        ),
    }


def signal_work(app):
    event = getattr(app.state, "library_schedule_event", None)
    if event is not None:
        event.set()


async def wake_node(app, node_id):
    session = app.state.registry.get_session(node_id)
    if session is None or not getattr(session, "library_wakeup", False):
        return False

    async def send():
        async with session.send_lock:
            await session.ws.send_text(encode("library_wake", {}, v=session.protocol_v))

    try:
        await asyncio.wait_for(send(), timeout=2)
        return True
    except Exception:
        return False  # Durable queue and HTTP polling remain authoritative.


async def scheduler(app):
    last_wake = {}
    while True:
        app.state.library_schedule_event.clear()
        rows = await app.state.library.rows(
            "SELECT target_node_id,MIN(transfer_id) AS first_task FROM library_transfers WHERE "
            "(state IN ('pending','waiting') AND COALESCE(next_retry_ts,0)<=?) OR "
            "(state='running' AND lease_until<=?) GROUP BY target_node_id",
            (time.time(), time.time()),
        )
        now = time.monotonic()
        active = {row["target_node_id"] for row in rows}
        last_wake = {key: value for key, value in last_wake.items() if key in active}
        for row in rows:
            key = row["target_node_id"]
            previous = last_wake.get(key)
            if (
                previous is None or previous[0] != row["first_task"] or now - previous[1] >= 5
            ) and await wake_node(app, key):
                last_wake[key] = (row["first_task"], now)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(app.state.library_schedule_event.wait(), timeout=1)


async def resource_status(lib, resource_id):
    rows = await lib.rows(
        "SELECT r.*,v.ingest_state,v.resource_type,v.size_bytes,v.sha256 FROM library_resources r JOIN library_resource_versions v ON r.resource_id=v.resource_id AND r.current_version=v.version WHERE r.resource_id=?",
        (resource_id,),
    )
    if not rows:
        raise HTTPException(404, "Library resource not found")
    result = rows[0]
    placements = await lib.rows(
        "SELECT p.*,n.online,n.node_name FROM library_placements p JOIN nodes n ON n.node_id=p.node_id WHERE p.resource_id=? AND p.version=?",
        (resource_id, result["current_version"]),
    )
    desired = [p for p in placements if p["desired_state"] == "present"]
    ready = sum(p["observed_state"] == "ready" for p in desired)
    total = len(desired)
    if result["lifecycle"] != "active":
        status = result["lifecycle"]
    elif result["ingest_state"] != "staged":
        status = result["ingest_state"]
    elif total and ready == total:
        status = "ready"
    elif ready:
        status = "partially_ready"
    elif desired and all(p["observed_state"] == "failed" for p in desired):
        status = "failed"
    else:
        status = "distributing" if desired else "staged"
    result.update(
        remaining_nodes=len({p["node_id"] for p in placements if p["observed_state"] != "absent"}),
        status=status,
        ready_count=ready,
        target_count=total,
        placement_summary=f"{ready}/{total}",
        placements=placements,
    )
    result["transfers"] = await lib.rows(
        "SELECT transfer_id,target_node_id,action,route,source_node_id,state,attempts,confirmed_bytes,next_retry_ts,last_error,fallback_reason FROM library_transfers WHERE resource_id=? AND version=?",
        (resource_id, result["current_version"]),
    )
    result["gc_errors"] = await lib.rows(
        "SELECT last_error FROM library_upload_sessions WHERE resource_id=? AND last_error IS NOT NULL",
        (resource_id,),
    )
    return result


@router.get("/resources/{resource_id}")
async def get_resource(resource_id: str, request: Request):
    return await resource_status(service(request), resource_id)


@router.get("/resources")
async def list_resources(
    request: Request,
    limit: int = Query(default=1000, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    folder_id: str | None = None,
):
    lib = service(request)
    condition = "deleted_ts IS NULL AND lifecycle<>'deleted'"
    args = ()
    if folder_id is not None:
        condition += " AND folder_id=?"
        args = (folder_id,)
    rows = await lib.rows(
        f"SELECT resource_id FROM library_resources WHERE {condition} ORDER BY created_ts DESC,resource_id LIMIT ? OFFSET ?",
        (*args, limit, offset),
    )
    total = (
        await lib.rows(f"SELECT COUNT(*) AS total FROM library_resources WHERE {condition}", args)
    )[0]["total"]
    resources = []
    for row in rows:
        try:
            resources.append(await resource_status(lib, row["resource_id"]))
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            # GC may finish between the page query and fetching an individual row.
    return {
        "resources": resources,
        "total": total,
        "limit": limit,
        "offset": offset,
    }
