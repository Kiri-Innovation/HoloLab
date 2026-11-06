"""Durable artifact migration plans and node-wide maintenance fences.

The conservative first version drains the entire source node, including for a
single artifact. Physical work belongs to the local maintenance worker.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from pathlib import Path

from hololab.persistence.db import Database

LIVE = ("pending", "assigned", "running", "orphaned")


def contains(root: str, path: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def canonical(path: str) -> str:
    if not Path(path).is_absolute():
        raise ValueError("Absolute paths are required")
    return os.path.normpath(path)


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class MigrationStore:
    def __init__(self, db: Database):
        self.db = db

    async def rows(self, sql, args=()):
        async with self.db.read() as conn, conn.execute(sql, args) as cur:
            names = [d[0] for d in cur.description]
            return [dict(zip(names, row, strict=True)) for row in await cur.fetchall()]

    async def get(self, mid):
        rows = await self.rows("SELECT * FROM artifact_migrations WHERE migration_id=?", (mid,))
        if not rows:
            raise ValueError("Migration not found")
        row = rows[0]
        row["plan"] = json.loads(row.pop("plan_json"))
        row["units"] = await self.rows(
            "SELECT * FROM artifact_migration_units WHERE migration_id=?", (mid,)
        )
        row["aliases"] = await self.rows(
            "SELECT * FROM artifact_path_aliases WHERE migration_id=?", (mid,)
        )
        return row

    async def plan(
        self,
        *,
        node_id,
        source,
        target,
        handle_id=None,
        workspace=False,
        adopt_existing=False,
        preserve_existing_missing=False,
    ):
        source, target = canonical(source), canonical(target)
        if contains(source, target) or contains(target, source):
            raise ValueError("Source and destination must be disjoint")
        if Path(source).is_symlink() or not Path(source).exists():
            raise ValueError("Source must exist and must not be a compatibility alias")
        if Path(target).is_symlink():
            raise ValueError("Destination must not be a symlink")
        handles = await self.rows(
            "SELECT handle_id,node_id,path,kind,location_version,deleted_ts FROM handles"
        )
        if handle_id:
            selected = [
                h for h in handles if h["handle_id"] == handle_id and h["deleted_ts"] is None
            ]
            if not selected or selected[0]["node_id"] != node_id:
                raise ValueError("Unknown active handle on source node")
            source = canonical(selected[0]["path"])
        # Enlarge to cover every registered enclosing directory. This prevents
        # a shard move from leaving its aggregate directory view behind.
        changed = True
        while changed:
            changed = False
            for h in handles:
                if h["node_id"] == node_id and h["deleted_ts"] is None and h["kind"] == "dir":
                    hp = canonical(h["path"])
                    if hp != source and contains(hp, source):
                        source, changed = hp, True
        if contains(source, target) or contains(target, source):
            raise ValueError("Expanded storage unit overlaps destination")
        if Path(source).is_symlink():
            raise ValueError("A symlink artifact requires migrating its enclosing directory")
        affected = [h for h in handles if contains(source, canonical(h["path"]))]
        if any(h["node_id"] != node_id for h in affected):
            raise ValueError("Storage is also registered to another node")
        active = [h for h in affected if h["deleted_ts"] is None]
        # Outermost registered objects are independent storage units. Workspace
        # residual files are copied as part of the envelope, never discarded.
        roots = []
        for h in sorted(active, key=lambda h: len(h["path"])):
            hp = canonical(h["path"])
            if not any(contains(p, hp) for p in roots):
                roots.append(hp)
        if not roots:
            roots = [source]
        source_stat = os.lstat(source)
        parent = Path(target)
        while not parent.exists():
            parent = parent.parent
        from hololab.artifact_migration_files import preflight

        survey = await asyncio.to_thread(preflight, source, target, preserve_existing_missing)
        plan = dict(
            preserve_existing_missing=preserve_existing_missing,
            preflight=survey,
            node_id=node_id,
            source=source,
            target=target,
            scope="workspace" if workspace else "artifact",
            adopt_existing=adopt_existing,
            source_identity=[source_stat.st_dev, source_stat.st_ino],
            target_device=os.stat(parent).st_dev,
            handles=[
                dict(
                    handle_id=h["handle_id"],
                    old_path=h["path"],
                    new_path=target + h["path"][len(source) :],
                    expected_version=h["location_version"],
                )
                for h in affected
            ],
            roots=roots,
        )
        mid = str(uuid.uuid4())
        now = time.time()

        async def write(conn):
            await conn.execute(
                "INSERT INTO artifact_migrations(migration_id,node_id,source,target,scope,plan_digest,plan_json,created_ts,updated_ts) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    mid,
                    node_id,
                    source,
                    target,
                    plan["scope"],
                    digest(plan),
                    json.dumps(plan),
                    now,
                    now,
                ),
            )
            ids = {}
            for root in roots:
                uid = str(uuid.uuid4())
                ids[root] = uid
                await conn.execute(
                    "INSERT INTO artifact_migration_units(unit_id,migration_id,source,target) VALUES (?,?,?,?)",
                    (uid, mid, root, target + root[len(source) :]),
                )
            for h in plan["handles"]:
                root = next((r for r in roots if contains(r, h["old_path"])), roots[0])
                await conn.execute(
                    "INSERT INTO artifact_migration_handles VALUES (?,?,?,?,?,?)",
                    (
                        mid,
                        ids[root],
                        h["handle_id"],
                        h["old_path"],
                        h["new_path"],
                        h["expected_version"],
                    ),
                )

        await self.db.write(write)
        return await self.get(mid)

    async def claim(self, mid, plan_digest, key):
        now = time.time()

        async def write(conn):
            async with conn.execute(
                "SELECT node_id,source,state,plan_digest,idempotency_key,epoch FROM artifact_migrations WHERE migration_id=?",
                (mid,),
            ) as cur:
                row = await cur.fetchone()
            if not row or row[3] != plan_digest:
                raise ValueError("Plan digest mismatch")
            node, source, state, _, existing_key, epoch = row
            if existing_key and existing_key != key:
                raise ValueError("Idempotency key mismatch")
            if state in ("completed", "cleanup_pending", "compatibility_ready", "committed"):
                return
            if state in ("aborted", "rolled_back"):
                raise ValueError("Migration is terminal")
            async with conn.execute(
                "SELECT job_id FROM jobs WHERE state IN ('pending','assigned','running','orphaned') AND (node_id=? OR node_id IS NULL) LIMIT 1",
                (node,),
            ) as cur:
                if await cur.fetchone():
                    raise ValueError("Drain source node jobs before freezing writes")
            async with conn.execute(
                "SELECT 1 FROM artifact_cleanup_claims WHERE node_id=? LIMIT 1", (node,)
            ) as cur:
                if await cur.fetchone():
                    raise ValueError("Artifact cleanup must finish before migration")
            await conn.execute(
                "INSERT INTO artifact_migration_claims VALUES (?,?,?,?,?) ON CONFLICT(migration_id) DO UPDATE SET epoch=excluded.epoch",
                (mid, node, source, epoch + 1, now),
            )
            await conn.execute(
                "UPDATE artifact_migrations SET state='claimed',epoch=epoch+1,idempotency_key=?,error=NULL,updated_ts=? WHERE migration_id=?",
                (key, now, mid),
            )

        await self.db.write(write)
        return await self.get(mid)

    async def state(self, mid, state, error=None):
        async def write(conn):
            await conn.execute(
                "UPDATE artifact_migrations SET state=?,error=?,updated_ts=? WHERE migration_id=?",
                (state, error, time.time(), mid),
            )
            await conn.execute(
                "UPDATE artifact_migration_units SET state=? WHERE migration_id=?", (state, mid)
            )

        await self.db.write(write)

    async def commit(self, mid, epoch=None, cancel_event=None):
        async def write(conn):
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("Migration cancelled before commit")
            if epoch is not None:
                async with conn.execute(
                    "SELECT epoch FROM artifact_migration_claims WHERE migration_id=?", (mid,)
                ) as cur:
                    claim = await cur.fetchone()
                if claim is None or claim[0] != epoch:
                    raise ValueError("Migration lease was superseded")
                async with conn.execute(
                    "SELECT 1 FROM artifact_migration_units WHERE migration_id=? AND (epoch<>? OR lease_until<? OR receipt_json IS NULL) LIMIT 1",
                    (mid, epoch, time.time()),
                ) as cur:
                    if await cur.fetchone():
                        raise ValueError("Migration lease expired or receipt missing")
            async with conn.execute(
                "SELECT state FROM artifact_migrations WHERE migration_id=?", (mid,)
            ) as cur:
                state = (await cur.fetchone())[0]
            if state == "committed":
                return
            if state != "prepared":
                raise ValueError("Migration is not prepared")
            async with conn.execute(
                "SELECT handle_id,old_path,new_path,expected_version FROM artifact_migration_handles WHERE migration_id=?",
                (mid,),
            ) as cur:
                handles = await cur.fetchall()
            for hid, old, new, version in handles:
                cur = await conn.execute(
                    "UPDATE handles SET path=?,location_version=location_version+1 WHERE handle_id=? AND path=? AND location_version=?",
                    (new, hid, old, version),
                )
                if cur.rowcount != 1:
                    raise ValueError("Handle changed since plan; refusing partial commit")
            await conn.execute(
                "UPDATE artifact_migrations SET state='committed',updated_ts=? WHERE migration_id=?",
                (time.time(), mid),
            )
            await conn.execute(
                "UPDATE artifact_migration_units SET state='committed' WHERE migration_id=?", (mid,)
            )

        await self.db.write(write)
