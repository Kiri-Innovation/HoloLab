"""Additive release annotations. Never UPDATE snapshots or their frozen evidence."""

import json
from pathlib import Path

from hololab.pack_publish import now
from hololab.package_identity import canonical_name


async def annotate(db, release, source: str) -> int:
    async def write(conn):
        rows = await (await conn.execute("SELECT snapshot_id,graph_json FROM snapshots")).fetchall()
        inserted = 0
        for snapshot_id, raw in rows:
            for node in json.loads(raw).get("nodes", []):
                version = node.get("algorithm_version", "")
                identity = node.get("code_identity") or {}
                if (
                    not version.endswith("+")
                    or version[:-1] != release.version
                    or canonical_name(node.get("algorithm_name")) != release.name
                    or identity.get("sha256") != release.code_sha256
                ):
                    continue
                note = {
                    "name": release.name,
                    "version": release.version,
                    "code_sha256": release.code_sha256,
                    "source": source,
                    "published_at": release.published_at,
                    "annotated_at": now(),
                }
                cursor = await conn.execute(
                    "INSERT OR IGNORE INTO published_as_notes VALUES (?, ?, ?, ?, ?)",
                    (snapshot_id, node["id"], release.name, release.version, json.dumps(note)),
                )
                inserted += cursor.rowcount
        return inserted

    return await db.write(write)


async def notes_for(db, snapshot_id: str | None) -> dict:
    if snapshot_id is None:
        return {}
    async with db.read() as conn:
        rows = await (
            await conn.execute(
                "SELECT graph_node_id,note_json FROM published_as_notes WHERE snapshot_id=? ORDER BY published_name,published_version",
                (snapshot_id,),
            )
        ).fetchall()
    result = {}
    for node, raw in rows:
        result.setdefault(node, []).append(json.loads(raw))
    return result


async def annotate_path(path: Path, release, source: str) -> dict:
    from hololab.persistence.db import open_database

    if not path.exists():
        return {"status": "no_snapshot_database", "count": 0}
    db = await open_database(path)
    try:
        return {"status": "annotated", "count": await annotate(db, release, source)}
    finally:
        await db.close()
