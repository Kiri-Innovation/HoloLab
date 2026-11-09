"""Gateway-local logical-device assignments, independent of draft revisions."""

from __future__ import annotations

import time

from pydantic import BaseModel, ConfigDict, Field, StrictStr

from hololab.persistence.db import Database


class DeviceBindingPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_revision: int = Field(strict=True, ge=0)
    # Null removes a binding; absent keys are unchanged.
    bindings: dict[StrictStr, StrictStr | None]


class DeviceBindingConflict(Exception):
    def __init__(self, current):
        self.current = current
        super().__init__("device binding revision changed")


class DeviceBindingStore:
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    async def _read(conn, workflow_id):
        async with conn.execute(
            "SELECT 1 FROM workflows WHERE workflow_id=?", (workflow_id,)
        ) as cursor:
            if await cursor.fetchone() is None:
                raise KeyError(workflow_id)
        async with conn.execute(
            "SELECT revision FROM workflow_device_binding_revisions WHERE workflow_id=?",
            (workflow_id,),
        ) as cursor:
            row = await cursor.fetchone()
        async with conn.execute(
            "SELECT logical_id,node_id FROM workflow_device_bindings "
            "WHERE workflow_id=? ORDER BY logical_id",
            (workflow_id,),
        ) as cursor:
            bindings = dict(await cursor.fetchall())
        return {"workflow_id": workflow_id, "revision": row[0] if row else 0, "bindings": bindings}

    async def get(self, workflow_id):
        async with self.db.read() as conn:
            # Database.read pins all SELECTs to a single SQLite snapshot.
            return await self._read(conn, workflow_id)

    async def patch(self, workflow_id, patch: DeviceBindingPatch):
        if any(
            not key.strip() or (value is not None and not value.strip())
            for key, value in patch.bindings.items()
        ):
            raise ValueError("logical and physical device IDs must be nonempty")

        async def write(conn):
            current = await self._read(conn, workflow_id)
            if current["revision"] != patch.base_revision:
                raise DeviceBindingConflict(current)
            now = time.time()
            for logical_id, node_id in patch.bindings.items():
                if node_id is None:
                    await conn.execute(
                        "DELETE FROM workflow_device_bindings WHERE workflow_id=? AND logical_id=?",
                        (workflow_id, logical_id),
                    )
                else:
                    await conn.execute(
                        "INSERT INTO workflow_device_bindings VALUES (?,?,?,?) "
                        "ON CONFLICT(workflow_id,logical_id) DO UPDATE SET "
                        "node_id=excluded.node_id,updated_ts=excluded.updated_ts",
                        (workflow_id, logical_id, node_id, now),
                    )
            await conn.execute(
                "INSERT INTO workflow_device_binding_revisions VALUES (?,?) "
                "ON CONFLICT(workflow_id) DO UPDATE SET revision=excluded.revision",
                (workflow_id, current["revision"] + 1),
            )
            return await self._read(conn, workflow_id)

        return await self.db.write(write)
