"""Mid-flight WS drop must not leave in-flight jobs stuck.

Regression guard for the 100-shard ``image-undistort`` fanout stall
(parent ``7d1f7305-6960-4311-ba86-3ba93675420d``): a keepalive-ping timeout
dropped the node↔gateway socket for ~2 s while shards were mid-flight.
Before this change, the WS-drop path only called ``mark_offline`` — it
left the node's ``assigned`` / ``running`` job rows untouched, and
neither the register-time reconciler nor the orphan sweeper touched
rows still in those states. The fanout's ``_await_job_terminal`` then
spun until its 24-hour hard timeout (per shard, per stuck row).

Three defects, three cover cases:

    1. Mid-flight orphan flip: on disconnect the node's ``assigned`` /
       ``running`` rows must flip to ``orphaned`` so reconcile can
       finalise them.
    2. ``_dispatch_prepared_shard`` / ``_dispatch_job`` — the row is
       written ASSIGNED before the WS ``send_text``. When send raises
       (dead socket) the row must be finalised as FAILED, not stranded
       ASSIGNED forever.
    3. ``_await_job_terminal`` — ``INTERRUPTED`` is terminal per the
       state machine and must exit the polling loop; otherwise the
       reconcile flip lands but the fanout worker still spins.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from hololab.gateway.execution import (
    _await_job_terminal,
    _dispatch_job,
    _dispatch_prepared_shard,
)
from hololab.gateway.jobs import Job, JobState
from hololab.gateway.registry import JobsStore, NodeSession
from hololab.gateway.workflows import GraphNode
from hololab.persistence.db import open_database
from hololab.protocol.messages import GpuInfo, JobFailReason

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_session(*, node_id: str, send_side_effect: Any = None) -> NodeSession:
    ws = MagicMock()
    ws.send_text = AsyncMock(side_effect=send_side_effect)
    return NodeSession(
        node_id=node_id,
        session_id="sess-1",
        node_name=f"{node_id}-name",
        ws=ws,
        advertised_url=None,
        gpu=GpuInfo(),
        packs=[],
        protocol_v=1,
        workspace_root="/tmp/ws-not-used-in-test",
    )


class _StubRegistry:
    """Minimal registry stub for ``_dispatch_prepared_shard`` / ``_dispatch_job``."""

    def __init__(self, session: NodeSession) -> None:
        self._session = session

    def get_session(self, node_id: str) -> NodeSession | None:
        return self._session if node_id == self._session.node_id else None


class _StubHub:
    """Records ``broadcast`` calls without needing a real subscriber pump."""

    def __init__(self) -> None:
        self.pushed: list[Any] = []

    def broadcast(self, frame: str) -> None:  # matches FrontendHub API
        self.pushed.append(frame)


# ---------------------------------------------------------------------------
# Defect 3: _await_job_terminal treats INTERRUPTED as terminal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_await_job_terminal_returns_on_interrupted(tmp_path: Path) -> None:
    """A shard flipped to INTERRUPTED by reconcile-on-register must
    unblock the fanout worker immediately — not wait for the 24 h
    hard timeout.
    """

    db = await open_database(tmp_path / "s.sqlite")
    try:

        async def _write(conn: Any) -> None:
            await conn.execute(
                "INSERT INTO jobs (job_id, workflow_id, algorithm_name, "
                "algorithm_version, params_json, input_handles_json, state, "
                "node_id, created_ts, updated_ts) "
                "VALUES ('j', 'w', 'demo', '0.1.0', '{}', '{}', 'interrupted', "
                "'n1', ?, ?)",
                (time.time(), time.time()),
            )

        await db.write(_write)
        store = JobsStore(db)

        # Would raise if it waited past the timeout. Use a tiny budget
        # so the test fails loudly on regression (missed terminal).
        final = await _await_job_terminal(store, "j", timeout_s=1.0, poll_s=0.05)
        assert final.state is JobState.INTERRUPTED
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_await_job_terminal_keeps_polling_on_orphaned(tmp_path: Path) -> None:
    """``ORPHANED`` is NOT terminal — the register-time reconciler can
    still hoist it back to RUNNING. Treating it as terminal would drop
    still-alive work on the floor.
    """

    from hololab.gateway.execution import WorkflowRunError

    db = await open_database(tmp_path / "s.sqlite")
    try:

        async def _write(conn: Any) -> None:
            await conn.execute(
                "INSERT INTO jobs (job_id, workflow_id, algorithm_name, "
                "algorithm_version, params_json, input_handles_json, state, "
                "node_id, created_ts, updated_ts) "
                "VALUES ('j', 'w', 'demo', '0.1.0', '{}', '{}', 'orphaned', "
                "'n1', ?, ?)",
                (time.time(), time.time()),
            )

        await db.write(_write)
        store = JobsStore(db)

        with pytest.raises(WorkflowRunError, match="did not terminate"):
            await _await_job_terminal(store, "j", timeout_s=0.3, poll_s=0.05)
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Defect 2: send_text failure finalises the row instead of stranding it
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_prepared_shard_send_fail_marks_failed(tmp_path: Path) -> None:
    """WS send raises (dead socket) — the shard row must be flipped
    FAILED (SYSTEM_ERROR) and the exception re-raised so the fanout
    aggregator can count it.
    """

    class _BoomWS(Exception):
        pass

    session = _make_session(node_id="n1", send_side_effect=_BoomWS("ws closed"))
    registry = _StubRegistry(session)
    hub = _StubHub()

    db = await open_database(tmp_path / "s.sqlite")
    try:
        store = JobsStore(db)
        shard = Job(
            job_id="shard-1",
            workflow_id="w",
            snapshot_id="s",
            algorithm_name="demo",
            algorithm_version="0.1.0",
            graph_node_id="fan",
            parent_job_id="parent-1",
            shard_element_id="e0",
        )
        await store.create(shard)

        gnode = GraphNode(
            id="fan",
            algorithm_name="demo",
            algorithm_version="0.1.0",
            assigned_node_id="n1",
        )

        with pytest.raises(_BoomWS):
            await _dispatch_prepared_shard(
                registry=registry,  # type: ignore[arg-type]
                store=store,
                hub=hub,  # type: ignore[arg-type]
                gnode=gnode,
                shard=shard,
                shard_element_id="e0",
                shard_output_prefix="/tmp/ws-not-used-in-test/parent",
            )

        final = await store.get("shard-1")
        assert final is not None
        assert final.state is JobState.FAILED
        assert final.fail_reason is JobFailReason.SYSTEM_ERROR
        assert "send failed" in (final.fail_message or "")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_dispatch_job_send_fail_marks_failed(tmp_path: Path) -> None:
    """Non-fanout single-dispatch path has the same defect and same fix
    — the row is written ASSIGNED before ``send_text``; a send failure
    must not leave it stranded.
    """

    class _BoomWS(Exception):
        pass

    session = _make_session(node_id="n1", send_side_effect=_BoomWS("closed"))
    registry = _StubRegistry(session)
    hub = _StubHub()

    db = await open_database(tmp_path / "s.sqlite")
    try:
        store = JobsStore(db)
        gnode = GraphNode(
            id="only",
            algorithm_name="demo",
            algorithm_version="0.1.0",
            assigned_node_id="n1",
            params={},
        )

        with pytest.raises(_BoomWS):
            await _dispatch_job(
                registry=registry,  # type: ignore[arg-type]
                store=store,
                hub=hub,  # type: ignore[arg-type]
                snapshot_id="snap-1",
                workflow_id="w",
                gnode=gnode,
                input_handles={},
                pack_entry=None,
                snapshot_jobs=None,
            )

        # Only one job row exists — grab it and check it landed FAILED.
        rows = await store.list_recent(limit=10)
        assert len(rows) == 1
        final = await store.get(rows[0]["job_id"])
        assert final is not None
        assert final.state is JobState.FAILED
        assert final.fail_reason is JobFailReason.SYSTEM_ERROR
        assert "send failed" in (final.fail_message or "")
    finally:
        await db.close()
