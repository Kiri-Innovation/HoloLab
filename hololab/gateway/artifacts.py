"""Gateway helpers for the Artifacts page.

The gateway is the coordinator: it holds handle metadata (via HandleBook) and
knows which node produced each row. Actual filesystem stat + rm happens on
the node — the gateway sends a batched WS request and awaits the reply
(see ``handle_check_req`` / ``artifact_delete_req`` in the protocol).

Two derived states are computed here, without going to disk:

    ``deleted``  — DB row has a non-null ``deleted_ts``. This means the
                   Artifacts page (or an agent) explicitly cleaned the
                   artifact via the dedicated flow. We keep the row so
                   past run history stays coherent.
    ``pending``  — Live liveness wasn't requested. Callers that opt in
                   with ``?check=1`` on the REST endpoint get one of
                   ``alive`` / ``incomplete`` / ``dead`` per row instead.

Grouping / summary rollups live here rather than in ``app.py`` so the API
surface stays a thin transport shell.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import HTTPException

from hololab.gateway.handles import Handle
from hololab.protocol import (
    ArtifactDeleteReq,
    HandleCheckItem,
    HandleCheckReq,
)
from hololab.protocol.envelope import encode

# Public state vocabulary — the frontend expects exactly these strings.
State = Literal["alive", "incomplete", "dead", "deleted", "pending"]

# How long to wait for a node to answer a batched liveness request. Node
# does stat() per handle; hundreds of stats in a burst are still cheap.
DEFAULT_CHECK_TIMEOUT_S = 8.0

# How long to wait for a single artifact delete. rm -rf on a big model
# directory can genuinely take several seconds; we cap generously.
DEFAULT_DELETE_TIMEOUT_S = 30.0


@dataclass
class ArtifactRow:
    """One row rendered by the Artifacts page.

    Kept intentionally flat so the JSON encoder doesn't have to know about
    nested Handle vs Job details — the endpoint layer just dumps this to
    the client.
    """

    handle_id: str
    node_id: str
    node_name: str | None
    workflow_id: str | None
    workflow_name: str | None
    snapshot_id: str | None
    job_id: str | None
    algorithm_name: str | None
    algorithm_version: str | None
    output_port_name: str | None
    tags: list[str]
    storage: str
    path: str
    size_bytes: int | None
    created_ts: float
    deleted_ts: float | None
    state: State
    # Populated only when the caller asked for a live check AND the node
    # returned data for this row. May stay None even when the check ran
    # (e.g. the node timed out) — the state is what drives the UI.
    live_size_bytes: int | None = None
    live_mtime: float | None = None


def derived_state(handle: Handle) -> State:
    """DB-cheap state derivation — no filesystem access.

    Used by list endpoints that don't opt into ``?check=1``. Also the
    fallback state when a live check times out for a specific handle.
    """

    return "deleted" if handle.deleted_ts is not None else "pending"


async def await_reply(
    app: Any,
    pending_dict_name: str,
    req_id: str,
    *,
    timeout_s: float,
) -> dict[str, Any]:
    """Small wrapper around the pending-future protocol.

    ``pending_dict_name`` is the attribute on ``app.state`` that holds
    the ``dict[req_id, Future]`` map. This helper centralises the
    "install + await + cleanup" boilerplate so REST endpoints stay
    linear.
    """

    loop = asyncio.get_running_loop()
    fut: asyncio.Future[dict[str, Any]] = loop.create_future()
    getattr(app.state, pending_dict_name)[req_id] = fut
    try:
        return await asyncio.wait_for(fut, timeout=timeout_s)
    finally:
        getattr(app.state, pending_dict_name).pop(req_id, None)


async def check_handles_live(
    app: Any,
    node_session: Any,
    handles: list[Handle],
    *,
    timeout_s: float = DEFAULT_CHECK_TIMEOUT_S,
) -> dict[str, dict[str, Any]]:
    """Fire one ``handle_check_req`` batch, return {handle_id: result-dict}.

    Skips handles marked ``deleted`` — those don't need a stat, the DB is
    already authoritative. Returns an empty dict if the node isn't online
    (caller falls back to DB-derived state).
    """

    live_targets = [h for h in handles if h.deleted_ts is None]
    if not live_targets or node_session is None:
        return {}

    req_id = str(uuid.uuid4())
    payload = HandleCheckReq(
        req_id=req_id,
        handles=[
            HandleCheckItem(handle_id=h.handle_id, path=h.path, storage=h.storage)
            for h in live_targets
        ],
    )
    # Install BEFORE sending — see delete_artifact for the same
    # ordering rationale.
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[dict[str, Any]] = loop.create_future()
    app.state.pending_handle_checks[req_id] = fut

    frame = encode("handle_check_req", payload, v=node_session.protocol_v)
    try:
        async with node_session.send_lock:
            await node_session.ws.send_text(frame)
    except Exception:
        app.state.pending_handle_checks.pop(req_id, None)
        return {}  # node dropped the frame; UI shows ``pending`` for these

    try:
        resp = await asyncio.wait_for(fut, timeout=timeout_s)
    except TimeoutError:
        app.state.pending_handle_checks.pop(req_id, None)
        return {}

    return {row["handle_id"]: row for row in resp.get("results", [])}


async def delete_artifact(
    app: Any,
    node_session: Any,
    handle: Handle,
    *,
    timeout_s: float = DEFAULT_DELETE_TIMEOUT_S,
) -> dict[str, Any]:
    """Issue one ``artifact_delete_req`` and await the ack.

    The node validates the path is under a configured root before rm.
    On success we mark the handle deleted in the DB — the row survives
    for history.
    """

    if node_session is None:
        raise HTTPException(
            status_code=409, detail=f"producing node {handle.node_id!r} is not connected"
        )

    req_id = str(uuid.uuid4())
    payload = ArtifactDeleteReq(
        req_id=req_id,
        handle_id=handle.handle_id,
        path=handle.path,
        storage=handle.storage,
    )
    # Install the pending future BEFORE we send — otherwise a fast
    # response (or a synchronous test double) can arrive at the
    # dispatcher before the future is registered.
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[dict[str, Any]] = loop.create_future()
    app.state.pending_artifact_deletes[req_id] = fut

    frame = encode("artifact_delete_req", payload, v=node_session.protocol_v)
    try:
        async with node_session.send_lock:
            await node_session.ws.send_text(frame)
    except Exception as exc:
        app.state.pending_artifact_deletes.pop(req_id, None)
        raise HTTPException(status_code=502, detail=f"send failed: {exc}") from exc

    try:
        resp = await asyncio.wait_for(fut, timeout=timeout_s)
    except TimeoutError as exc:
        app.state.pending_artifact_deletes.pop(req_id, None)
        raise HTTPException(status_code=504, detail="node did not reply in time") from exc

    if not resp.get("ok"):
        raise HTTPException(
            status_code=502,
            detail=resp.get("error") or "node refused the delete",
        )

    return resp


def state_from_check(resp_row: dict[str, Any] | None, handle: Handle) -> State:
    """Combine a live-check row with the DB-level ``deleted_ts`` bit.

    Rows explicitly deleted always win — the file might have been
    re-created externally but the user asked us to hide it, so we honour
    that.
    """

    if handle.deleted_ts is not None:
        return "deleted"
    if resp_row is None:
        return "pending"
    state = resp_row.get("state", "pending")
    if state in ("alive", "incomplete", "dead"):
        return state  # type: ignore[return-value]
    return "pending"


def rollup_states(rows: list[ArtifactRow]) -> dict[str, int]:
    """Count states in a row list. Used for summary + per-run badges."""

    counts: dict[str, int] = {}
    for r in rows:
        counts[r.state] = counts.get(r.state, 0) + 1
    return counts


def format_row(
    handle: Handle,
    *,
    node_name: str | None,
    workflow_id: str | None,
    workflow_name: str | None,
    snapshot_id: str | None,
    algorithm_name: str | None,
    algorithm_version: str | None,
    state: State,
    live_row: dict[str, Any] | None = None,
) -> ArtifactRow:
    """Assemble the flat wire shape from the DB pieces."""

    return ArtifactRow(
        handle_id=handle.handle_id,
        node_id=handle.node_id,
        node_name=node_name,
        workflow_id=workflow_id,
        workflow_name=workflow_name,
        snapshot_id=snapshot_id,
        job_id=handle.job_id,
        algorithm_name=algorithm_name,
        algorithm_version=algorithm_version,
        output_port_name=handle.output_port_name,
        tags=list(handle.tags),
        storage=handle.storage,
        path=handle.path,
        size_bytes=handle.size_bytes,
        created_ts=handle.created_ts,
        deleted_ts=handle.deleted_ts,
        state=state,
        live_size_bytes=(live_row or {}).get("size_bytes"),
        live_mtime=(live_row or {}).get("mtime"),
    )


def now_ts() -> float:
    """Wall clock, split out so tests can freeze time cleanly."""

    return time.time()
