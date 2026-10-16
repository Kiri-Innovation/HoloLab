"""Node-outbound, durable namespace changes; registry commits after every receipt."""

from __future__ import annotations

import json
import time
import uuid
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from hololab.gateway.library import FolderCreate, name_key, service
from hololab.gateway.library_distribution import wake_node
from hololab.gateway.library_transfers import authenticate

router = APIRouter(prefix="/api/library", tags=["library-management"])


async def rows(conn, sql, args=()):
    async with conn.execute(sql, args) as cur:
        return [
            dict(zip([c[0] for c in cur.description], row, strict=True))
            for row in await cur.fetchall()
        ]


async def folder_path(conn, folder_id):
    parts = []
    while folder_id != "root":
        found = await rows(conn, "SELECT * FROM library_folders WHERE folder_id=?", (folder_id,))
        if not found:
            raise HTTPException(404, "Library folder not found")
        item = found[0]
        parts.insert(0, item["name"])
        folder_id = item["parent_id"]
    return "/".join(parts)


async def finish(conn, op):
    if op["state"] == "completed":
        return
    pending = await rows(
        conn,
        "SELECT 1 FROM library_operation_nodes WHERE operation_id=? AND state<>'completed'",
        (op["operation_id"],),
    )
    if pending:
        return
    # Release the namespace fence inside this same transaction, before changes.
    await conn.execute(
        "UPDATE library_operations SET state='completed' WHERE operation_id=?",
        (op["operation_id"],),
    )
    if op["kind"] == "delete_folder":
        await conn.execute("DELETE FROM library_folders WHERE folder_id=?", (op["entity_id"],))
        return
    table, key = (
        ("folders", "folder_id") if op["kind"] == "rename_folder" else ("resources", "resource_id")
    )
    await conn.execute(
        f"UPDATE library_{table} SET name=?,normalized_name=? WHERE {key}=?",
        (op["new_name"], name_key(op["new_name"]), op["entity_id"]),
    )
    for p in await rows(
        conn, "SELECT resource_id,version,node_id,relative_path FROM library_placements"
    ):
        old = p["relative_path"]
        if old == op["old_path"] or old.startswith(op["old_path"] + "/"):
            await conn.execute(
                "UPDATE library_placements SET relative_path=? WHERE resource_id=? AND version=? AND node_id=?",
                (
                    op["new_path"] + old[len(op["old_path"]) :],
                    p["resource_id"],
                    p["version"],
                    p["node_id"],
                ),
            )


async def operation(lib, oid):
    found = await lib.rows("SELECT * FROM library_operations WHERE operation_id=?", (oid,))
    if not found:
        raise HTTPException(404, "Directory operation not found")
    return {
        **found[0],
        "nodes": await lib.rows(
            "SELECT p.node_id,n.node_name,n.online,p.state,p.attempts,p.last_error FROM library_operation_nodes p JOIN nodes n USING(node_id) WHERE operation_id=?",
            (oid,),
        ),
    }


async def begin(request, kind, entity_id, name=None):
    lib = service(request)
    if getattr(lib, "download_pins", {}):
        raise HTTPException(
            409, "Wait for browser downloads to finish before changing directory paths"
        )

    async def write(conn):
        if getattr(lib, "download_pins", {}):
            raise HTTPException(409, "Browser downloads are active; retry when they finish")
        if await rows(conn, "SELECT 1 FROM library_operations WHERE state='pending'"):
            raise HTTPException(
                409, "Another directory operation is pending; wait for node acknowledgements"
            )
        if await rows(
            conn,
            "SELECT 1 FROM library_transfers WHERE state NOT IN ('completed','cancelled','failed') UNION ALL SELECT 1 FROM library_upload_sessions WHERE state NOT IN ('completed','cancelled','failed') UNION ALL SELECT 1 FROM library_resources WHERE lifecycle<>'active'",
        ):
            raise HTTPException(
                409,
                "Wait for uploads, distribution and deletions to finish before changing directory paths",
            )
        if kind.endswith("folder"):
            if entity_id == "root":
                raise HTTPException(409, "The Library root cannot be renamed or deleted")
            found = await rows(
                conn, "SELECT * FROM library_folders WHERE folder_id=?", (entity_id,)
            )
            if not found:
                raise HTTPException(404, "Library folder not found")
            item = found[0]
            parent = item["parent_id"]
            old = await folder_path(conn, entity_id)
            if kind == "delete_folder" and await rows(
                conn,
                "SELECT 1 FROM library_folders WHERE parent_id=? UNION ALL SELECT 1 FROM library_resources WHERE folder_id=?",
                (entity_id, entity_id),
            ):
                raise HTTPException(
                    409,
                    "Folder is not empty. Delete its files and subfolders first, and wait for all deletion acknowledgements.",
                )
        else:
            found = await rows(
                conn, "SELECT * FROM library_resources WHERE resource_id=?", (entity_id,)
            )
            if not found:
                raise HTTPException(404, "Library resource not found")
            item = found[0]
            parent = item["folder_id"]
            old = "/".join(filter(None, [await folder_path(conn, parent), item["name"]]))
        if name is not None:
            if name_key(name) != item["normalized_name"]:
                await lib._check_name(conn, parent, name_key(name))
            if name == item["name"]:
                return None
        new = "/".join(filter(None, [await folder_path(conn, parent), name])) if name else None
        op = dict(
            operation_id=str(uuid.uuid4()),
            kind=kind,
            entity_id=entity_id,
            old_path=old,
            new_path=new,
            new_name=name,
            state="pending",
            created_ts=time.time(),
        )
        # Include every registered node for folders: an empty physical directory
        # can remain after its last resource was garbage-collected.
        nodes = (
            await rows(conn, "SELECT node_id FROM nodes")
            if kind.endswith("folder")
            else await rows(
                conn,
                "SELECT DISTINCT node_id FROM library_placements WHERE resource_id=? AND observed_state<>'absent'",
                (entity_id,),
            )
        )
        payloads = []
        for node in nodes:
            placements = await rows(
                conn,
                "SELECT p.*,v.size_bytes,v.sha256 FROM library_placements p JOIN library_resource_versions v USING(resource_id,version) WHERE p.node_id=?",
                (node["node_id"],),
            )
            entries = []
            for p in placements:
                path = p["relative_path"]
                if path == old or path.startswith(old + "/"):
                    if p["observed_state"] not in ("ready", "absent"):
                        raise HTTPException(
                            409, "All affected placements must be ready before renaming"
                        )
                    ids = await rows(
                        conn,
                        "SELECT transfer_id FROM library_transfers WHERE resource_id=? AND version=? AND target_node_id=? AND action='download'",
                        (p["resource_id"], p["version"], node["node_id"]),
                    )
                    entries.append({**p, "download_ids": [r["transfer_id"] for r in ids]})
            payloads.append((node["node_id"], json.dumps({**op, "entries": entries})))
        await conn.execute(
            "INSERT INTO library_operations VALUES (?,?,?,?,?,?,?,?)", tuple(op.values())
        )
        for node, payload in payloads:
            await conn.execute(
                "INSERT INTO library_operation_nodes(operation_id,node_id,payload) VALUES (?,?,?)",
                (op["operation_id"], node, payload),
            )
        await finish(conn, op)
        return op["operation_id"]

    async with lib.namespace_lock:
        oid = await lib.db.write(write)
    if oid is None:
        return {"state": "completed", "nodes": []}
    result = await operation(lib, oid)
    for node in result["nodes"]:
        await wake_node(request.app, node["node_id"])
    return result


@router.post("/folders/{folder_id}/rename", status_code=202)
async def rename_folder(folder_id: str, body: FolderCreate, request: Request):
    return await begin(request, "rename_folder", folder_id, body.name)


@router.post("/resources/{resource_id}/rename", status_code=202)
async def rename_resource(resource_id: str, body: FolderCreate, request: Request):
    return await begin(request, "rename_resource", resource_id, body.name)


@router.delete("/folders/{folder_id}", status_code=202)
async def delete_folder(folder_id: str, request: Request):
    return await begin(request, "delete_folder", folder_id)


@router.get("/operations")
async def operations(request: Request):
    lib = service(request)
    return {
        "operations": [
            await operation(lib, r["operation_id"])
            for r in await lib.rows(
                "SELECT operation_id FROM library_operations WHERE state='pending'"
            )
        ]
    }


@router.get("/operations/{operation_id}")
async def get_operation(operation_id: str, request: Request):
    return await operation(service(request), operation_id)


@router.post("/operations/{operation_id}/retry")
async def retry(operation_id: str, request: Request):
    lib = service(request)
    await lib.execute(
        "UPDATE library_operation_nodes SET state='pending',attempts=0,next_retry_ts=0,last_error=NULL WHERE operation_id=? AND state='failed'",
        (operation_id,),
    )
    return await operation(lib, operation_id)


@router.post("/nodes/{node_id}/operations/claim")
async def claim(
    node_id: str, request: Request, authorization: Annotated[str | None, Header()] = None
):
    lib = service(request)
    await authenticate(lib, node_id, authorization)

    async def write(conn):
        found = await rows(
            conn,
            "SELECT * FROM library_operation_nodes WHERE node_id=? AND state='pending' AND next_retry_ts<=? ORDER BY rowid LIMIT 1",
            (node_id, time.time()),
        )
        if not found:
            return {"operation": None}
        task = found[0]
        await conn.execute(
            "UPDATE library_operation_nodes SET next_retry_ts=? WHERE operation_id=? AND node_id=?",
            (time.time() + 120, task["operation_id"], node_id),
        )
        return {"operation": json.loads(task["payload"])}

    return await lib.db.write(write)


class Receipt(BaseModel):
    error: str | None = None


@router.post("/nodes/{node_id}/operations/{operation_id}/receipt")
async def receipt(
    node_id: str,
    operation_id: str,
    body: Receipt,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
):
    lib = service(request)
    await authenticate(lib, node_id, authorization)

    async def write(conn):
        found = await rows(
            conn,
            "SELECT * FROM library_operation_nodes WHERE operation_id=? AND node_id=?",
            (operation_id, node_id),
        )
        if not found:
            raise HTTPException(404, "Directory operation assignment not found")
        row = found[0]
        if row["state"] != "completed":
            attempts = row["attempts"] + 1
            state = (
                "completed" if body.error is None else ("failed" if attempts >= 5 else "pending")
            )
            await conn.execute(
                "UPDATE library_operation_nodes SET state=?,attempts=?,next_retry_ts=?,last_error=? WHERE operation_id=? AND node_id=?",
                (
                    state,
                    attempts,
                    time.time() + min(300, 2**attempts),
                    body.error[:2000] if body.error else None,
                    operation_id,
                    node_id,
                ),
            )
        op = (
            await rows(
                conn, "SELECT * FROM library_operations WHERE operation_id=?", (operation_id,)
            )
        )[0]
        await finish(conn, op)

    await lib.db.write(write)
    return await operation(lib, operation_id)
