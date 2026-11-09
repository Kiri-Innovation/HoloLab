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


async def resolve_devices(app, graph, *, workflow_id=None, required=None, probe=True, strict=True):
    """Resolve into an execution copy only; never persist an automatic assignment.

    Explicit logical bindings win. Legacy API assignments remain usable when
    there is no binding. Automatic candidates offer the exact package/operation,
    pass the declared capability contract, and are ordered by active job count.
    """
    from hololab.gateway.capabilities import check_workflow
    from hololab.gateway.workflows import WorkflowGraph, is_task_node
    from hololab.package_identity import offered_identity

    state = (
        await DeviceBindingStore(app.state.db).get(workflow_id)
        if workflow_id
        else {"revision": None, "bindings": {}}
    )
    async with (
        app.state.db.read() as conn,
        conn.execute(
            "SELECT node_id,COUNT(*) FROM jobs "
            "WHERE state IN ('pending','assigned','running','orphaned') "
            "GROUP BY node_id"
        ) as cursor,
    ):
        loads = dict(await cursor.fetchall())
    sessions = sorted(
        app.state.registry.all_sessions(), key=lambda s: (loads.get(s.node_id, 0), s.node_id)
    )
    reports, issues, assignments, routing = {}, [], {}, {}
    for node in graph.nodes:
        if not is_task_node(node):
            continue
        needed = required is None or node.id in required
        bound = state["bindings"].get(node.device)
        target = bound or node.assigned_node_id
        routing[node.id] = "binding" if bound else "legacy" if target else "automatic"
        candidates = [app.state.registry.get_session(target)] if target else sessions
        failures = []
        selected = None
        for session in candidates:
            if session is None:
                failures.append(f"compute node {target!r} is not online")
                continue
            if node.kind == "intrinsic":
                if node.operation not in session.intrinsics:
                    failures.append(f"{session.node_id}: operation {node.operation} unavailable")
                    continue
            else:
                try:
                    offered_identity(session.packs, node.algorithm_name, node.algorithm_version)
                except ValueError as exc:
                    failures.append(f"{session.node_id}: {exc}")
                    continue
            candidate = node.model_copy(update={"assigned_node_id": session.node_id})
            if needed and probe and node.kind == "algorithm" and not target:
                checked = await check_workflow(app, WorkflowGraph(nodes=[candidate]), fresh=True)
                report = checked[node.id]
                if report["status"] in ("missing", "unknown"):
                    failures.append(f"{session.node_id}: {report['status']}")
                    continue
                reports[node.id] = report
            selected = session.node_id
            break
        if selected is None:
            message = f"{node.id}: no suitable online execution node for device {node.device!r}" + (
                ": " + "; ".join(failures) if failures else ""
            )
            if needed and strict:
                raise ValueError(message)
            if needed:
                issues.append({"kind": "device_resolution", "node_id": node.id, "message": message})
            node.assigned_node_id = target
            continue
        node.assigned_node_id = selected
        assignments[node.id] = selected
    return {
        "workflow_id": workflow_id,
        "revision": state["revision"],
        "assignments": assignments,
        "routing": routing,
        "reports": reports,
        "issues": issues,
    }


async def verify_device_revision(app, workflow_id, execution):
    from fastapi import HTTPException

    evidence = execution.get("device_bindings", {})
    current = await DeviceBindingStore(app.state.db).get(workflow_id)
    if evidence.get("revision") != current["revision"]:
        raise HTTPException(409, "Device bindings changed during preparation; prepare again")
