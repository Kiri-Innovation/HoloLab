"""Browser downloads: bounded peer Range reads, or the existing outbound relay."""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from hololab.gateway.library import CHUNK_SIZE, service
from hololab.gateway.library_distribution import resource_status, signal_work
from hololab.library_transport import capability

router = APIRouter(prefix="/api/library", tags=["library-download"])


async def relay(lib, resource, node):
    async def write(conn):
        from hololab.gateway.library_management import rows

        active = await rows(
            conn,
            "SELECT transfer_id FROM library_transfers WHERE resource_id=? AND action='relay_upload' AND state IN ('pending','running','waiting')",
            (resource["resource_id"],),
        )
        if active:
            return active[0]["transfer_id"]
        used = (
            await rows(
                conn, "SELECT COALESCE(SUM(reserved_bytes),0) AS used FROM library_upload_sessions"
            )
        )[0]["used"]
        needed = resource["size_bytes"] * 2
        if (
            resource["size_bytes"] > lib.config.max_file_bytes
            or used + needed > lib.config.staging_quota_bytes
        ):
            raise HTTPException(
                507,
                "Download relay staging quota exceeded; free staging space or increase the quota",
            )
        tid, uid = str(uuid.uuid4()), str(uuid.uuid4())
        now = time.time()
        await conn.execute(
            "INSERT INTO library_upload_sessions(upload_id,resource_id,version,expected_size,expected_sha256,chunk_size,reserved_bytes,staging_key,created_ts,last_activity_ts,purpose,uploader_node_id) VALUES (?,?,?,?,?,?,?,?,?,?,'relay',?)",
            (
                uid,
                resource["resource_id"],
                resource["current_version"],
                resource["size_bytes"],
                resource["sha256"],
                CHUNK_SIZE,
                needed,
                uid,
                now,
                now,
                node["node_id"],
            ),
        )
        await conn.execute(
            "INSERT INTO library_transfers(transfer_id,resource_id,version,target_node_id,source_ref,delete_epoch,action,route,source_node_id,fallback_reason) VALUES (?,?,?,?,?,?,'relay_upload','gateway_relay',?,'browser_download')",
            (
                tid,
                resource["resource_id"],
                resource["current_version"],
                node["node_id"],
                uid,
                resource["delete_epoch"],
                node["node_id"],
            ),
        )
        return tid

    return await lib.db.write(write)


def interval(header, size):
    if not header:
        return 0, size - 1, 200
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header)
    if not match or not any(match.groups()):
        raise HTTPException(416, "Only a single byte range is supported")
    left, right = match.groups()
    start = int(left) if left else max(0, size - int(right))
    end = min(size - 1, int(right)) if left and right else size - 1
    if start > end or start >= size:
        raise HTTPException(
            416,
            "Download range is outside the resource",
            headers={"Content-Range": f"bytes */{size}"},
        )
    return start, end, 206


@router.get("/resources/{resource_id}/download")
async def download(resource_id: str, request: Request):
    lib = service(request)
    pins = getattr(lib, "download_pins", None)
    if pins is None:
        pins = lib.download_pins = {}
    pins[resource_id] = pins.get(resource_id, 0) + 1

    def unpin():
        pins[resource_id] -= 1
        if not pins[resource_id]:
            del pins[resource_id]

    try:
        async with lib.namespace_lock:
            if await lib.rows("SELECT 1 FROM library_operations WHERE state='pending'"):
                raise HTTPException(
                    409, "Directory paths are changing; retry after node acknowledgements"
                )
            resource = await resource_status(lib, resource_id)
            if resource["lifecycle"] != "active" or resource["ingest_state"] != "staged":
                raise HTTPException(409, "Resource is not available for download")
            start, end, status = interval(request.headers.get("range"), resource["size_bytes"])
    except BaseException:
        unpin()
        raise
    client = httpx.AsyncClient(timeout=httpx.Timeout(30, connect=3), trust_env=False)
    source = None
    first = None
    peer = None

    async def staged():
        for row in await lib.rows(
            "SELECT storage_key FROM library_staging_objects WHERE resource_id=? AND version=? AND state='ready'",
            (resource_id, resource["current_version"]),
        ):
            try:
                return (lib.root / row["storage_key"]).open("rb")
            except FileNotFoundError:
                pass
        return None

    async def peer_read(node, offset, last):
        token = capability(
            node["node_token"],
            path=node["relative_path"],
            sha256=resource["sha256"],
            size=resource["size_bytes"],
        )
        response = await client.send(
            client.build_request(
                "GET",
                node["advertised_url"].rstrip("/") + "/_library/content",
                headers={"X-Library-Capability": token, "Range": f"bytes={offset}-{last}"},
            ),
            stream=True,
        )
        if (
            response.status_code != 206
            or response.headers.get("content-range")
            != f"bytes {offset}-{last}/{resource['size_bytes']}"
        ):
            await response.aclose()
            raise ValueError("Source returned an invalid byte range")
        return response

    try:
        source = await staged()
        route = "gateway_staging"
        nodes = await lib.rows(
            "SELECT p.*,n.advertised_url,n.node_token,n.online FROM library_placements p JOIN nodes n USING(node_id) WHERE p.resource_id=? AND p.version=? AND p.desired_state='present' AND p.observed_state='ready' ORDER BY n.online DESC",
            (resource_id, resource["current_version"]),
        )
        if source is None and end >= start:
            for node in nodes:
                if not node["advertised_url"] or not node["node_token"]:
                    continue
                try:
                    first = await peer_read(node, start, min(end, start + CHUNK_SIZE - 1))
                    peer = node
                    route = "peer_direct"
                    break
                except (httpx.HTTPError, ValueError):
                    pass
        if source is None and peer is None:
            if not nodes:
                raise HTTPException(503, "No verified resource copy is available")
            tid = await relay(lib, resource, nodes[0])
            signal_work(request.app)
            route = "gateway_relay"
            for _ in range(120):
                source = await staged()
                if source:
                    break
                task = await lib.rows(
                    "SELECT state,last_error FROM library_transfers WHERE transfer_id=?", (tid,)
                )
                if task and task[0]["state"] == "failed":
                    raise HTTPException(503, "Download relay failed: " + str(task[0]["last_error"]))
                if await request.is_disconnected():
                    raise HTTPException(499, "Download client disconnected")
                await asyncio.sleep(0.25)
            if source is None:
                raise HTTPException(
                    503,
                    "Download relay is preparing the file; retry shortly. Source devices must be online.",
                    headers={"Retry-After": "5"},
                )
        if source:
            source.seek(start)
    except BaseException:
        if source:
            source.close()
        if first:
            await first.aclose()
        await client.aclose()
        unpin()
        raise

    async def stream():
        response = first
        try:
            offset = start
            while offset <= end:
                if source:
                    block = await asyncio.to_thread(source.read, min(64 * 1024, end - offset + 1))
                    if not block:
                        raise OSError("Download source truncated")
                    offset += len(block)
                    yield block
                else:
                    last = min(end, offset + CHUNK_SIZE - 1)
                    response = response or await peer_read(peer, offset, last)
                    remaining = last - offset + 1
                    async for block in response.aiter_bytes(64 * 1024):
                        if len(block) > remaining:
                            raise OSError("Peer exceeded requested range")
                        remaining -= len(block)
                        offset += len(block)
                        yield block
                    await response.aclose()
                    response = None
                    if remaining:
                        raise OSError("Peer truncated requested range")
        finally:
            if response:
                await response.aclose()
            if source:
                source.close()
            await client.aclose()
            unpin()

    headers = {
        "Content-Disposition": "attachment; filename*=UTF-8''" + quote(resource["name"], safe=""),
        "Content-Length": str(end - start + 1),
        "Accept-Ranges": "bytes",
        "ETag": '"' + resource["sha256"] + '"',
        "X-Library-Route": route,
        "Cache-Control": "no-store",
    }
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{resource['size_bytes']}"
    return StreamingResponse(
        stream(), status_code=status, media_type="application/octet-stream", headers=headers
    )
