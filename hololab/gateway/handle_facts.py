"""Durable, compact display facts for artifact handles."""
from __future__ import annotations
import asyncio, json, time
from dataclasses import replace
from typing import Any
from hololab.gateway.handle_summary import summarize_handle

SCHEMA_VERSION = 1

async def persist(app: Any, handle: Any, dim_labels: list[str] | None) -> None:
    """Probe outside the event loop, then atomically persist the small result."""
    from hololab.type_registry import registry
    registry.refresh()
    generation = registry.generation
    try:
        facts = await asyncio.to_thread(summarize_handle, handle, depth=len(dim_labels or []) or None, dim_labels=dim_labels)
        # ``summarize_handle`` deliberately represents an unreadable
        # directory as a compact ``fields.error`` response for its on-demand
        # summary API.  Durable facts have an explicit three-state contract,
        # so that result is a failed probe, not ready display evidence.
        probe_error = facts.get("fields", {}).get("error")
        if isinstance(probe_error, str) and probe_error:
            facts, status, error = {}, "failed", probe_error
        else:
            status, error = "ready", None
    except Exception as exc:  # facts must never turn job completion into failure
        facts, status, error = {}, "failed", str(exc)
    async def write(conn):
        await conn.execute("""INSERT INTO handle_facts(handle_id,dim_labels_json,tags_json,facts_json,schema_version,status,error,updated_ts)
        VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(handle_id) DO UPDATE SET dim_labels_json=excluded.dim_labels_json,tags_json=excluded.tags_json,facts_json=excluded.facts_json,schema_version=excluded.schema_version,status=excluded.status,error=excluded.error,updated_ts=excluded.updated_ts""",
        (handle.handle_id,json.dumps(dim_labels or []),json.dumps(handle.tags),json.dumps({**facts, "_type_generation": generation}),SCHEMA_VERSION,status,error,time.time()))
    await app.state.db.write(write)

async def batch(app: Any, handle_ids: list[str]) -> list[dict[str, Any]]:
    if not handle_ids: return []
    from hololab.type_registry import registry
    registry.refresh()
    generation = registry.generation
    marks=','.join('?' for _ in handle_ids)
    async with app.state.db.read() as conn:
        rows=await (await conn.execute(f"SELECT handle_id,dim_labels_json,tags_json,facts_json,schema_version,status,error FROM handle_facts WHERE handle_id IN ({marks})",handle_ids)).fetchall()
    got={r[0]:r for r in rows}
    # Upgrade rows written before ``persist`` distinguished a compact
    # summary's ``fields.error`` from successful facts.  Return failed on the
    # very first read and repair the durable row so a later client cannot see
    # stale ready evidence.
    corrections: list[tuple[str, str]] = []
    result: list[dict[str, Any]] = []
    for handle_id in handle_ids:
        row = got.get(handle_id)
        if row is None:
            result.append({"handle_id": handle_id, "status": "pending"})
            continue
        facts = json.loads(row[3] or "{}")
        if facts.pop("_type_generation", None) != generation:
            result.append({"handle_id": handle_id, "status": "pending"})
            continue
        probe_error = facts.get("fields", {}).get("error") if row[5] == "ready" else None
        if isinstance(probe_error, str) and probe_error:
            corrections.append((probe_error, handle_id))
            result.append({"handle_id": handle_id, "dim_labels": json.loads(row[1]),
                "tags": json.loads(row[2]), "facts": {}, "schema_version": row[4],
                "status": "failed", "error": probe_error})
            continue
        result.append({"handle_id": handle_id, "dim_labels": json.loads(row[1]),
            "tags": json.loads(row[2]), "facts": facts, "schema_version": row[4],
            "status": row[5], "error": row[6]})
    if corrections:
        async def write(conn):
            await conn.executemany("UPDATE handle_facts SET facts_json='{}', status='failed', error=?, updated_ts=? WHERE handle_id=?",
                [(error, time.time(), handle_id) for error, handle_id in corrections])
        await app.state.db.write(write)
    return result
