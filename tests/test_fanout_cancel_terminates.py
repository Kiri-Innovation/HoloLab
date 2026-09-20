"""Cancelling a fan-out mid-flight must halt the dispatch loop.

Regression for the "cancel-all doesn't stop the loop; new colmap keeps
spawning" bug. Trigger:

    * 100-shard fan-out running with parallelism=4 on a busy box.
    * Operator hits ``POST /api/jobs/cancel-all`` — DB flips every live
      row to ``CANCELLED``, WS ``job_cancel`` frames go out for the
      already-ASSIGNED shards, and ``_cascade_cancel_shards`` marks the
      remaining PENDING shards CANCELLED.
    * Bug: ``_run_one`` was using the in-memory ``shard`` object it
      received from Phase 1 (state=PENDING) and never re-reading the
      DB after acquiring the semaphore. When the next task grabbed the
      semaphore, ``_dispatch_prepared_shard`` transitioned that stale
      PENDING → ASSIGNED and **overwrote** the DB's CANCELLED with
      ASSIGNED, then sent ``job_assign`` to the node — which happily
      started another shard.

The fix reads shard + parent state from the DB inside ``_run_one``
after acquiring the semaphore. If the shard is no longer PENDING (or
the parent is CANCELLED), we do NOT dispatch. This test locks that
behaviour in.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from hololab.gateway.app import create_app
from hololab.gateway.execution import WorkflowRunError, run_snapshot
from hololab.gateway.workflows import WorkflowGraph
from tests.test_fanout_parallelism import (
    _fake_online_node,
    _install_fake_catalog,
    _seed_fanout_graph,
)


def _count_kinds(frames: list[str]) -> dict[str, int]:
    """Bucket WS frame strings by their ``kind`` field."""
    out: dict[str, int] = {}
    for f in frames:
        try:
            env = json.loads(f)
        except (ValueError, TypeError):
            continue
        kind = env.get("kind", "?")
        out[kind] = out.get(kind, 0) + 1
    return out


@pytest.mark.asyncio
async def test_parent_cancel_halts_dispatch_loop(tmp_path: Path) -> None:
    """After ``cancel-all``, no further ``job_assign`` frames are sent
    even though 7 shards were still PENDING waiting on the semaphore."""

    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    app = create_app(db_path=tmp_path / "cxl.sqlite")

    elements = [f"e{i:02d}" for i in range(8)]
    parallelism = 1  # One shard in-flight at a time → cancel with 7 still PENDING.

    sent_frames: list[str] = []
    first_assign_seen = asyncio.Event()

    async def _capture_send(frame: str) -> None:
        sent_frames.append(frame)
        env = json.loads(frame)
        if env.get("kind") == "job_assign" and not first_assign_seen.is_set():
            first_assign_seen.set()

    with TestClient(app) as client:
        _fake_online_node(
            app, workspace_root=ws_root, send_hook=AsyncMock(side_effect=_capture_send)
        )
        _install_fake_catalog(app)

        async def _seed() -> tuple[str, WorkflowGraph]:
            snap_id, graph, _ = await _seed_fanout_graph(
                app, element_ids=elements, parallelism=parallelism
            )
            return snap_id, graph

        snapshot_id, graph = client.portal.call(_seed)

        async def _run_and_cancel() -> None:
            # Start the workflow — will hang inside _await_job_terminal
            # for shard 0 (the fake node never replies).
            async def _swallow_workflow_run_error() -> None:
                with contextlib.suppress(WorkflowRunError):
                    await run_snapshot(
                        app,
                        snapshot_id=snapshot_id,
                        graph=graph,
                        workflow_id="wf1",
                        skip_graph_nodes={"src"},
                        seed_outputs={"src": {"out": "h-arr"}},
                        job_timeout_s=30,
                    )

            snap_task = asyncio.create_task(_swallow_workflow_run_error())
            try:
                # Wait until the first job_assign frame reached the fake
                # node — one shard is now ASSIGNED, seven are PENDING.
                await asyncio.wait_for(first_assign_seen.wait(), timeout=5)

                # Cancel-all — cascade should flip every live row to
                # CANCELLED. The fake node doesn't reply to job_cancel,
                # but the gateway's optimistic DB transition suffices
                # for _await_job_terminal to observe CANCELLED and
                # release the semaphore.
                store = app.state.jobs_store
                from hololab.gateway.app import _cancel_many_by_rows

                live = []
                for state in ("pending", "assigned", "running", "orphaned"):
                    live.extend(await store.list_recent(limit=500, state=state))
                await _cancel_many_by_rows(app.state.registry, store, app.state.hub, live)

                # Wait for the workflow to unwind.
                await asyncio.wait_for(snap_task, timeout=10)
            finally:
                snap_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await snap_task

        client.portal.call(_run_and_cancel)

        # Post-cancel invariants ------------------------------------------------
        kinds = _count_kinds(sent_frames)
        # Exactly one job_assign was ever sent — the shard that was in
        # flight when the operator hit cancel. Zero further shards were
        # dispatched, even though seven were sitting on the semaphore.
        assert kinds.get("job_assign", 0) == 1, (
            f"expected exactly 1 job_assign before cancel, got {kinds} — "
            "dispatch loop did not honour parent cancel (regression)"
        )

        # DB state: parent + every shard is CANCELLED.
        async def _check_terminal_states() -> None:
            store = app.state.jobs_store
            rows = await store.list_recent(limit=500, order="asc")
            fan_rows = [r for r in rows if r["graph_node_id"] == "fan"]
            # 1 parent + 8 shards = 9 rows total in the "fan" graph node.
            assert len(fan_rows) == 9, [r["state"] for r in fan_rows]
            for r in fan_rows:
                assert r["state"] == "cancelled", r

        client.portal.call(_check_terminal_states)


@pytest.mark.asyncio
async def test_cancel_all_is_idempotent_across_repeat_calls(tmp_path: Path) -> None:
    """Calling cancel-all a second time after everything is already
    CANCELLED must be a no-op (0 new cancels) — no ghost dispatches, no
    exceptions. Users hit the button multiple times in a panic.
    """

    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    app = create_app(db_path=tmp_path / "cxl-idempotent.sqlite")

    elements = [f"e{i:02d}" for i in range(4)]

    sent_frames: list[str] = []
    first_assign_seen = asyncio.Event()

    async def _capture_send(frame: str) -> None:
        sent_frames.append(frame)
        env = json.loads(frame)
        if env.get("kind") == "job_assign" and not first_assign_seen.is_set():
            first_assign_seen.set()

    with TestClient(app) as client:
        _fake_online_node(
            app, workspace_root=ws_root, send_hook=AsyncMock(side_effect=_capture_send)
        )
        _install_fake_catalog(app)

        async def _seed() -> tuple[str, WorkflowGraph]:
            snap_id, graph, _ = await _seed_fanout_graph(app, element_ids=elements, parallelism=1)
            return snap_id, graph

        snapshot_id, graph = client.portal.call(_seed)

        async def _run_and_double_cancel() -> None:
            async def _swallow_workflow_run_error() -> None:
                with contextlib.suppress(WorkflowRunError):
                    await run_snapshot(
                        app,
                        snapshot_id=snapshot_id,
                        graph=graph,
                        workflow_id="wf1",
                        skip_graph_nodes={"src"},
                        seed_outputs={"src": {"out": "h-arr"}},
                        job_timeout_s=30,
                    )

            from hololab.gateway.app import _cancel_many_by_rows

            snap_task = asyncio.create_task(_swallow_workflow_run_error())
            try:
                await asyncio.wait_for(first_assign_seen.wait(), timeout=5)

                store = app.state.jobs_store
                # First cancel — flips the whole fan-out to CANCELLED.
                live = []
                for state in ("pending", "assigned", "running", "orphaned"):
                    live.extend(await store.list_recent(limit=500, state=state))
                result_1 = await _cancel_many_by_rows(
                    app.state.registry, store, app.state.hub, live
                )
                assert result_1["count"] >= 1

                await asyncio.wait_for(snap_task, timeout=10)

                # Second cancel — nothing left to cancel; count must be 0.
                live = []
                for state in ("pending", "assigned", "running", "orphaned"):
                    live.extend(await store.list_recent(limit=500, state=state))
                result_2 = await _cancel_many_by_rows(
                    app.state.registry, store, app.state.hub, live
                )
                assert result_2["count"] == 0, result_2
            finally:
                snap_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await snap_task

        client.portal.call(_run_and_double_cancel)
