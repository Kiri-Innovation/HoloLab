"""Snapshot-bound values and acknowledged per-consumer materialization."""

import asyncio
import json
import uuid

from hololab.constants import OPERATIONS, constant_value
from hololab.gateway.handles import Handle
from hololab.protocol import encode
from hololab.protocol.messages import EnsureConstantReq


async def ensure_input(app, snapshot_id, source, consumer):
    try:
        handle = await _ensure_input(app, snapshot_id, source, consumer)
    except ValueError as exc:
        message = str(exc)

        async def failed(conn):
            await conn.execute(
                "INSERT OR REPLACE INTO snapshot_preparation_errors VALUES (?, ?)",
                (snapshot_id, message),
            )

        await app.state.db.write(failed)
        raise

    async def cleared(conn):
        await conn.execute(
            "DELETE FROM snapshot_preparation_errors WHERE snapshot_id=?", (snapshot_id,)
        )

    await app.state.db.write(cleared)
    return handle


async def _ensure_input(app, snapshot_id, source, consumer):
    digest, value = constant_value(source.operation, source.params)
    port = OPERATIONS[source.operation][2]

    async def bind(conn):
        await conn.execute(
            "INSERT OR IGNORE INTO constant_values VALUES (?, ?)", (digest, json.dumps(value))
        )
        await conn.execute(
            "INSERT OR IGNORE INTO snapshot_constants VALUES (?, ?, ?, ?)",
            (snapshot_id, source.id, port, digest),
        )
        async with conn.execute(
            "SELECT digest FROM snapshot_constants WHERE snapshot_id=? AND graph_node_id=? AND output_port=?",
            (snapshot_id, source.id, port),
        ) as cur:
            row = await cur.fetchone()
        if row[0] != digest:
            raise ValueError("frozen constant differs from the snapshot value")

    await app.state.db.write(bind)
    return await ensure_value(app, snapshot_id, source.id, port, digest, value, consumer)


async def ensure_value(app, snapshot_id, source_id, port, digest, value, consumer):
    session = app.state.registry.get_session(consumer.assigned_node_id)
    context = f"常量输入准备失败: {source_id}.{port} → {consumer.assigned_node_id}"
    capability = "literal-v3" if value.get("contract_version") == 3 else "constant-v1"
    if session is None or capability not in session.intrinsics:
        raise ValueError(context + f": device is offline or lacks {capability} support")
    if not hasattr(app.state, "constant_locks"):
        app.state.constant_locks = {}
        app.state.pending_constants = {}
    lock = app.state.constant_locks.setdefault((digest, session.node_id), asyncio.Lock())
    async with lock:
        # Always ensure the device cache exists, including after external deletion.
        response = None
        for attempt in range(2):
            req_id = str(uuid.uuid4())
            fut = asyncio.get_running_loop().create_future()
            app.state.pending_constants[req_id] = (session, fut)
            try:
                async with session.send_lock:
                    await session.ws.send_text(
                        encode(
                            "ensure_constant_req",
                            EnsureConstantReq(req_id=req_id, digest=digest, value=value),
                            v=session.protocol_v,
                        )
                    )
                response = await asyncio.wait_for(fut, 30)
                break
            except (TimeoutError, OSError) as exc:
                if attempt:
                    raise ValueError(context + ": " + str(exc)) from exc
            finally:
                app.state.pending_constants.pop(req_id, None)
        if response.error or response.digest != digest or not response.path:
            raise ValueError(context + ": " + (response.error or "invalid device acknowledgement"))
        handle_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"hololab:constant:{session.node_id}:{digest}")
        )
        await app.state.handles.register(
            Handle(
                handle_id=handle_id,
                node_id=session.node_id,
                storage=value.get("storage", "dir"),
                tags=value["tags"],
                path=response.path,
                job_id=None,
                output_port_name=port,
            )
        )

        async def remember(conn):
            await conn.execute(
                "INSERT OR REPLACE INTO constant_materializations VALUES (?, ?, ?)",
                (digest, session.node_id, handle_id),
            )

        await app.state.db.write(remember)
        return handle_id


async def ensure_inline_inputs(app, snapshot_id, consumer, wired):
    # Snapshot binding is authoritative, including declared defaults. Never re-read draft/catalog.
    async with (
        app.state.db.read() as conn,
        conn.execute(
            "SELECT b.input_port, b.digest, v.value_json FROM snapshot_input_values b "
            "JOIN constant_values v ON v.digest=b.digest WHERE b.snapshot_id=? AND b.graph_node_id=?",
            (snapshot_id, consumer.id),
        ) as cur,
    ):
        rows = await cur.fetchall()
    for port, digest, raw in rows:
        if port in wired:
            continue
        try:
            wired[port] = await ensure_value(
                app, snapshot_id, consumer.id, port, digest, json.loads(raw), consumer
            )
        except ValueError as exc:
            message = str(exc)

            async def failed(conn, message=message):
                await conn.execute(
                    "INSERT OR REPLACE INTO snapshot_preparation_errors VALUES (?, ?)",
                    (snapshot_id, message),
                )

            await app.state.db.write(failed)
            raise

    async def cleared(conn):
        await conn.execute(
            "DELETE FROM snapshot_preparation_errors WHERE snapshot_id=?", (snapshot_id,)
        )

    await app.state.db.write(cleared)
    return wired
