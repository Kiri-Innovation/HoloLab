"""Durable, compact display facts for artifact handles."""
from __future__ import annotations
import asyncio, json, time
from dataclasses import replace
from typing import Any
from hololab.gateway.handle_summary import summarize_handle

SCHEMA_VERSION = 1

async def persist(app: Any, handle: Any, dim_labels: list[str] | None) -> None:
    """Probe outside the event loop, then atomically persist the small result."""
    try:
        facts = await asyncio.to_thread(summarize_handle, handle, depth=len(dim_labels or []) or None, dim_labels=dim_labels)
        status, error = "ready", None
    except Exception as exc:  # facts must never turn job completion into failure
        facts, status, error = {}, "failed", str(exc)
    async def write(conn):
        await conn.execute("""INSERT INTO handle_facts(handle_id,dim_labels_json,tags_json,facts_json,schema_version,status,error,updated_ts)
        VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(handle_id) DO UPDATE SET dim_labels_json=excluded.dim_labels_json,tags_json=excluded.tags_json,facts_json=excluded.facts_json,schema_version=excluded.schema_version,status=excluded.status,error=excluded.error,updated_ts=excluded.updated_ts""",
        (handle.handle_id,json.dumps(dim_labels or []),json.dumps(handle.tags),json.dumps(facts),SCHEMA_VERSION,status,error,time.time()))
    await app.state.db.write(write)

async def batch(app: Any, handle_ids: list[str]) -> list[dict[str, Any]]:
    if not handle_ids: return []
    marks=','.join('?' for _ in handle_ids)
    async with app.state.db.read() as conn:
        rows=await (await conn.execute(f"SELECT handle_id,dim_labels_json,tags_json,facts_json,schema_version,status,error FROM handle_facts WHERE handle_id IN ({marks})",handle_ids)).fetchall()
    got={r[0]:r for r in rows}
    return [{"handle_id":h,"status": "pending"} if h not in got else {"handle_id":h,"dim_labels":json.loads(got[h][1]),"tags":json.loads(got[h][2]),"facts":json.loads(got[h][3] or '{}'),"schema_version":got[h][4],"status":got[h][5],"error":got[h][6]} for h in handle_ids]
