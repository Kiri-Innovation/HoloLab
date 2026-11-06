"""Frozen-node migration coordinator shared by HTTP and the local worker."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import threading
import time
import uuid
from pathlib import Path

from hololab.artifact_migration_files import LocalMigration, set_preview_freeze
from hololab.artifact_migration_store import MigrationStore, contains

COMMITTED = {"committed", "compatibility_ready", "cleanup_pending", "completed"}


def copy_writers(source, target):
    """Reject known external copy writers; never signal them."""
    found = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            args = (proc / "cmdline").read_bytes().decode(errors="replace").split("\0")
        except OSError:
            continue
        if (
            args
            and Path(args[0]).name in {"cp", "rsync", "rclone"}
            and any(source in a or target in a for a in args[1:] if a)
        ):
            found.append(int(proc.name))
    return found


class MigrationService:
    def __init__(self, db, *, node_id, allowed_roots, gateway_url=None):
        self.store = MigrationStore(db)
        self.node_id = node_id
        self.allowed_roots = [os.path.normpath(str(p)) for p in allowed_roots]
        self.gateway_url = gateway_url
        self.tasks = {}
        self.cancel_events = {}

    def authorize(self, node_id, source):
        if node_id != self.node_id or not any(contains(r, source) for r in self.allowed_roots):
            raise ValueError("Migration worker only serves its configured local node and roots")

    async def plan(self, **kwargs):
        if not kwargs.get("source"):
            if kwargs.get("handle_id"):
                rows = await self.store.rows(
                    "SELECT path FROM handles WHERE handle_id=? AND node_id=?",
                    (kwargs["handle_id"], kwargs["node_id"]),
                )
                if not rows:
                    raise ValueError("Unknown local handle")
                kwargs["source"] = rows[0]["path"]
            elif len(self.allowed_roots) == 1:
                kwargs["source"] = self.allowed_roots[0]
            else:
                raise ValueError("An explicit source root is required")
        self.authorize(kwargs["node_id"], os.path.normpath(kwargs["source"]))
        kwargs["layout_root"] = max(
            (r for r in self.allowed_roots if contains(r, os.path.normpath(kwargs["source"]))),
            key=len,
        )
        result = await self.store.plan(**kwargs)
        self.authorize(result["node_id"], result["source"])
        return result

    async def status(self, mid):
        m = await self.store.get(mid)
        w = LocalMigration(m)
        journal = w.area / "state.json"
        if journal.exists():
            with contextlib.suppress(OSError, ValueError):
                m["local_progress"] = json.loads(journal.read_text())
        m["worker_running"] = mid in self.tasks and not self.tasks[mid].done()
        m["filesystem_locked"] = w.locked()
        return m

    async def start(self, mid, plan_digest, key, repair_existing=False):
        if mid in self.tasks and not self.tasks[mid].done():
            return await self.status(mid)
        m = await self.store.get(mid)
        self.authorize(m["node_id"], m["source"])
        if m["plan_digest"] != plan_digest:
            raise ValueError("Plan digest mismatch")
        if m["plan"].get("preflight", {}).get("blockers"):
            raise ValueError(
                "Plan contains unresolved blockers; repair the source and create a new plan"
            )
        if m["state"] in {"aborted", "rolled_back"}:
            raise ValueError("Migration is terminal")
        if m["idempotency_key"] and m["idempotency_key"] != key:
            raise ValueError("Idempotency key mismatch")
        self.cancel_events[mid] = threading.Event()
        self.tasks[mid] = asyncio.create_task(self.run(mid, plan_digest, key, repair_existing))
        return await self.status(mid)

    async def ensure_visible(self, m):
        if not self.gateway_url:
            return  # embedding applications/tests own their file service roots
        import httpx

        async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
            base = self.gateway_url.rstrip("/")
            path = f"{base}/api/nodes/{m['node_id']}/config"
            response = await client.get(path)
            response.raise_for_status()
            cfg = response.json().get("config", response.json())
            legacy = list(cfg.get("legacy_workspace_roots", []))
            target_root = m["plan"].get("target_root", m["target"])
            if target_root not in [cfg.get("workspace_root"), *legacy]:
                legacy.append(target_root)
                response = await client.patch(
                    path, json={"patch": {"legacy_workspace_roots": legacy}}
                )
                response.raise_for_status()
                result = response.json()
                if result.get("ok") is False:
                    raise ValueError("File service rejected target root")
            # Re-fetch to verify the actually applied configuration.
            response = await client.get(path)
            response.raise_for_status()
            cfg = response.json().get("config", response.json())
            if target_root not in [
                cfg.get("workspace_root"),
                *cfg.get("legacy_workspace_roots", []),
            ]:
                raise ValueError("Target root is not visible to file service")

    async def select_primary(self, m, primary):
        if not self.gateway_url:
            return
        import httpx

        async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
            url = f"{self.gateway_url.rstrip('/')}/api/nodes/{m['node_id']}/config"
            response = await client.get(url)
            response.raise_for_status()
            cfg = response.json().get("config", response.json())
            legacy = list(
                dict.fromkeys([*cfg.get("legacy_workspace_roots", []), m["source"], m["target"]])
            )
            legacy = [p for p in legacy if p != primary]
            if (
                cfg.get("workspace_root") != primary
                or cfg.get("legacy_workspace_roots", []) != legacy
            ):
                response = await client.patch(
                    url,
                    json={"patch": {"workspace_root": primary, "legacy_workspace_roots": legacy}},
                )
                response.raise_for_status()
            response = await client.get(url)
            response.raise_for_status()
            cfg = response.json().get("config", response.json())
            if cfg.get("workspace_root") != primary:
                raise ValueError("Primary workspace root did not converge")

    async def heartbeat(self, mid, token, epoch):
        while True:

            async def write(conn):
                await conn.execute(
                    "UPDATE artifact_migration_units SET lease_until=? WHERE migration_id=? AND lease_token=? AND epoch=?",
                    (time.time() + 120, mid, token, epoch),
                )

            await self.store.db.write(write)
            await asyncio.sleep(20)

    async def io(self, fn, *args, **kwargs):
        # Thread work must finish before a lock or database can be released.
        task = asyncio.create_task(asyncio.to_thread(fn, *args, **kwargs))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def run(self, mid, plan_digest, key, repair_existing=False):
        m = await self.store.get(mid)
        w = LocalMigration(m)
        w.stop = self.cancel_events.get(mid)
        try:
            with w.lock():
                writers = copy_writers(m["source"], m["target"])
                if writers:
                    raise ValueError(
                        f"External copy still running: {writers}; wait, do not kill it"
                    )
                if m["state"] in {"completed", "cleanup_pending", "compatibility_ready"}:
                    return
                if m["state"] != "committed":
                    m = await self.store.claim(mid, plan_digest, key)
                    await self.io(
                        set_preview_freeze,
                        Path(m["plan"].get("source_root", w.source)),
                        Path(m["plan"].get("target_root", w.target)),
                        mid,
                        True,
                    )
                    await self.ensure_visible(m)
                    token = str(uuid.uuid4())
                    epoch = m["epoch"]

                    async def lease(conn):
                        await conn.execute(
                            "UPDATE artifact_migration_units SET lease_token=?,lease_until=?,epoch=? WHERE migration_id=?",
                            (token, time.time() + 120, epoch, mid),
                        )

                    await self.store.db.write(lease)
                    beat = asyncio.create_task(self.heartbeat(mid, token, epoch))
                    try:
                        await self.store.state(mid, "copying")
                        receipt = await self.io(w.prepare, repair_existing=repair_existing)
                        await self.store.state(mid, "verifying")
                        await self.io(w.assert_unchanged)

                        async def receipts(conn):
                            await conn.execute(
                                "UPDATE artifact_migration_units SET receipt_json=?,state='prepared' WHERE migration_id=? AND epoch=?",
                                (json.dumps(receipt), mid, epoch),
                            )

                        await self.store.db.write(receipts)
                        await self.store.state(mid, "prepared")
                        await self.ensure_visible(m)
                        await self.io(w.assert_unchanged)
                        await self.store.commit(mid, epoch=epoch, cancel_event=w.stop)
                    finally:
                        beat.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await beat

                # Durable alias intent precedes exchange. Replaying after a
                # crash inspects inode ownership rather than guessing success.
                async def alias_intent(conn):
                    await conn.execute(
                        "INSERT INTO artifact_path_aliases VALUES (?,?,?,?,?,?) ON CONFLICT(old_path) DO NOTHING",
                        (
                            str(w.source),
                            str(w.target),
                            str(w.backup),
                            mid,
                            "pending",
                            json.dumps(m["plan"]["source_identity"]),
                        ),
                    )

                await self.store.db.write(alias_intent)
                await self.io(w.alias)
                if m["scope"] == "workspace":
                    await self.select_primary(m, m["target"])

                await self.io(
                    set_preview_freeze,
                    Path(m["plan"].get("source_root", w.source)),
                    Path(m["plan"].get("target_root", w.target)),
                    mid,
                    False,
                )

                async def finish(conn):
                    await conn.execute(
                        "UPDATE artifact_path_aliases SET state='ready' WHERE migration_id=?",
                        (mid,),
                    )
                    await conn.execute(
                        "UPDATE artifact_migrations SET state='cleanup_pending',error=NULL,updated_ts=? WHERE migration_id=?",
                        (time.time(), mid),
                    )
                    await conn.execute(
                        "UPDATE artifact_migration_units SET state='cleanup_pending',lease_token=NULL,lease_until=NULL WHERE migration_id=?",
                        (mid,),
                    )
                    await conn.execute(
                        "DELETE FROM artifact_migration_claims WHERE migration_id=?", (mid,)
                    )

                await self.store.db.write(finish)
        except Exception as exc:
            message = str(exc)
            current = await self.store.get(mid)
            if current["state"] not in COMMITTED:
                await self.store.state(
                    mid,
                    "aborted" if isinstance(exc, InterruptedError) else "retryable_failed",
                    str(exc),
                )
                if isinstance(exc, InterruptedError):

                    async def release(conn):
                        await conn.execute(
                            "DELETE FROM artifact_migration_claims WHERE migration_id=?", (mid,)
                        )

                    await self.store.db.write(release)
                    await self.io(
                        set_preview_freeze,
                        Path(m["plan"].get("source_root", w.source)),
                        Path(m["plan"].get("target_root", w.target)),
                        mid,
                        False,
                    )
            else:

                async def error(conn):
                    await conn.execute(
                        "UPDATE artifact_migrations SET error=? WHERE migration_id=?",
                        (message, mid),
                    )

                await self.store.db.write(error)

    async def idle(self, mid):
        task = self.tasks.get(mid)
        if task and not task.done():
            raise ValueError("Worker is running; wait before changing migration state")
        return await self.store.get(mid)

    async def cancel(self, mid):
        task = self.tasks.get(mid)
        if task is not None and not task.done():
            current = await self.store.get(mid)
            if current["state"] in COMMITTED:
                raise ValueError("Already committed; use rollback")
            self.cancel_events[mid].set()
            result = await self.status(mid)
            result["cancel_requested"] = True
            return result
        m = await self.idle(mid)
        if m["state"] in COMMITTED:
            raise ValueError("Already committed; use rollback")

        async def write(conn):
            await conn.execute(
                "UPDATE artifact_migrations SET state='aborted' WHERE migration_id=?", (mid,)
            )
            await conn.execute(
                "UPDATE artifact_migration_units SET state='aborted',lease_until=NULL WHERE migration_id=?",
                (mid,),
            )
            await conn.execute("DELETE FROM artifact_migration_claims WHERE migration_id=?", (mid,))

        await self.store.db.write(write)
        await self.io(
            set_preview_freeze,
            Path(m["plan"].get("source_root", m["source"])),
            Path(m["plan"].get("target_root", m["target"])),
            mid,
            False,
        )
        # Prepared destination is retained for review; source was not changed.
        return await self.status(mid)

    async def maintenance_claim(self, m):
        async def write(conn):
            async with conn.execute(
                "SELECT 1 FROM jobs WHERE state IN ('pending','assigned','running','orphaned') AND (node_id=? OR node_id IS NULL) LIMIT 1",
                (m["node_id"],),
            ) as cur:
                if await cur.fetchone():
                    raise ValueError("Drain node jobs before rollback/finalize")
            async with conn.execute(
                "SELECT 1 FROM artifact_cleanup_claims WHERE node_id=? LIMIT 1", (m["node_id"],)
            ) as cur:
                if await cur.fetchone():
                    raise ValueError("Cleanup in progress")
            await conn.execute(
                "INSERT INTO artifact_migration_claims VALUES (?,?,?,?,?) ON CONFLICT(migration_id) DO NOTHING",
                (m["migration_id"], m["node_id"], m["source"], m["epoch"], time.time()),
            )

        await self.store.db.write(write)

    async def rollback(self, mid):
        m = await self.idle(mid)
        if m["state"] == "rolled_back":
            return await self.status(mid)
        if m["state"] not in COMMITTED | {"rolling_back"} or m["state"] == "completed":
            raise ValueError("Rollback requires a committed migration with retained source")
        w = LocalMigration(m)
        with w.lock():
            await self.maintenance_claim(m)
            try:
                await self.io(w.verify_target, allow_preview_cache=True)
                journal = json.loads((w.area / "state.json").read_text())
                if journal.get("state") == "reclaiming":
                    raise ValueError("Source reclamation has begun; rollback is unavailable")
                if w.source.is_symlink():
                    await self.io(w.verify_backup)
            except BaseException:
                if m["state"] != "rolling_back":

                    async def release(conn):
                        await conn.execute(
                            "DELETE FROM artifact_migration_claims WHERE migration_id=?", (mid,)
                        )

                    await self.store.db.write(release)
                raise
            await self.store.state(mid, "rolling_back")
            await self.io(w.restore_alias)
            if m["scope"] == "workspace":
                await self.select_primary(m, m["source"])

            async def write(conn):
                await conn.execute(
                    "UPDATE artifact_migrations SET state='rolling_back' WHERE migration_id=?",
                    (mid,),
                )
                for h in m["plan"]["handles"]:
                    cur = await conn.execute(
                        "UPDATE handles SET path=?,location_version=location_version+1 WHERE handle_id=? AND path=? AND location_version=?",
                        (h["old_path"], h["handle_id"], h["new_path"], h["expected_version"] + 1),
                    )
                    if cur.rowcount != 1:
                        raise ValueError("Handle changed after commit; rollback refused")
                await conn.execute(
                    "UPDATE artifact_migrations SET state='rolled_back' WHERE migration_id=?",
                    (mid,),
                )
                await conn.execute(
                    "UPDATE artifact_migration_units SET state='rolled_back' WHERE migration_id=?",
                    (mid,),
                )
                await conn.execute(
                    "UPDATE artifact_path_aliases SET state='rolled_back' WHERE migration_id=?",
                    (mid,),
                )
                await conn.execute(
                    "DELETE FROM artifact_migration_claims WHERE migration_id=?", (mid,)
                )

            await self.store.db.write(write)
        return await self.status(mid)

    async def finalize(self, mid, delete_source=False):
        m = await self.idle(mid)
        if m["state"] == "completed":
            return await self.status(mid)
        if m["state"] != "cleanup_pending":
            raise ValueError("Migration must reach cleanup_pending first")
        if not delete_source:
            return await self.status(mid)  # explicit deletion authorization required
        w = LocalMigration(m)
        with w.lock():
            await self.maintenance_claim(m)
            try:
                await self.io(w.verify_target, allow_extra=True)
                journal = json.loads((w.area / "state.json").read_text())
                # A crash (or an external authorized cleanup) can remove the
                # backup before the database completion transaction. Reconcile
                # only after verifying the entire destination and alias.
                if os.path.lexists(w.backup) and journal.get("state") != "reclaiming":
                    await self.io(w.verify_backup)
                w.journal(state="reclaiming")
                await self.io(w.reclaim)
            except BaseException:
                # Finalize never changes the live target. Failed verification
                # must not leave a permanent node-wide dispatch freeze.
                async def release(conn):
                    await conn.execute(
                        "DELETE FROM artifact_migration_claims WHERE migration_id=?", (mid,)
                    )

                await self.store.db.write(release)
                raise

            async def write(conn):
                await conn.execute(
                    "UPDATE artifact_migrations SET state='completed' WHERE migration_id=?", (mid,)
                )
                await conn.execute(
                    "UPDATE artifact_migration_units SET state='completed' WHERE migration_id=?",
                    (mid,),
                )
                await conn.execute(
                    "UPDATE artifact_path_aliases SET state='source_reclaimed' WHERE migration_id=?",
                    (mid,),
                )
                await conn.execute(
                    "DELETE FROM artifact_migration_claims WHERE migration_id=?", (mid,)
                )

            await self.store.db.write(write)
            w.journal(state="completed")
        return await self.status(mid)

    async def close(self):
        # Graceful shutdown waits for durable copy work; no abandoned thread.
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
