"""Sequential DAG executor for workflow snapshots.

Executes one snapshot start-to-finish: topologically sorts the algorithm
nodes, dispatches them one at a time to their assigned compute nodes, and
threads each upstream job's output handles into the downstream node's
``input_handles``. Stops on the first failure.

Concurrency: this MVP is deliberately sequential. A single ``WorkflowRunner``
runs one snapshot per call; multiple runs against the same gateway are fine
because each ``run_snapshot`` is a bounded coroutine and holds no global
state beyond the DB writes it makes.

arrayed<T> fan-out: when a graph node's pack is ``arrayable`` and the
per-node ``arrayed_toggle`` is on, the executor forks the single dispatch
into N shard jobs — one per element of the arrayed inputs — under a
coordinator "parent" job. All shards write into the parent's workspace
keyed by element_id, so the aggregate output directory grows naturally
as shards complete; the gateway registers one output handle per port
pointing at the aggregate dir.

Concurrency inside a fan-out is bounded by ``GraphNode.parallelism``
(default 1 = serial). Every shard row is created in ``PENDING`` upfront
so the snapshot detail endpoint sees the full row set from the first
frame; dispatch then goes through ``asyncio.Semaphore(parallelism)`` and
``asyncio.gather(..., return_exceptions=True)`` — one shard's failure
does NOT short-circuit the pool. The parent is marked ``FAILED`` once,
after every shard has reached a terminal state, so downstream nodes are
never triggered by a partially-succeeded fan-out.

Not covered here (see ``docs/workflow-schema.md#non-goals``): parallel
branches, retries, rerun-with-changes.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from hololab.gateway.handles import Handle, HandleBook
from hololab.gateway.hub import FrontendHub
from hololab.gateway.jobs import Job, JobState, JobStateMachine, event_from_transition
from hololab.gateway.registry import JobsStore, NodeRegistry, SnapshotJobsStore
from hololab.gateway.workflows import (
    GraphNode,
    WorkflowGraph,
    effective_port_arrayed,
    packs_by_key_from_catalog,
    resolve_handle_output_tags,
    topological_order,
)
from hololab.logging import get_logger
from hololab.protocol import JobAssign, JobFailReason, encode

log = get_logger("gateway.exec")


class WorkflowRunError(RuntimeError):
    """Raised for run-time problems (upstream failed, node dropped, timeout)."""


def merged_params_with_defaults(
    gnode_params: dict[str, Any] | None,
    pack_entry: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge manifest ``params:`` defaults with ``gnode.params`` (gnode wins).

    A workflow draft records only the params the operator has touched. When
    the manifest evolves (e.g. ``frame-extraction`` gaining
    ``max_width`` / ``start_frame`` / ``end_frame`` after the workflow
    was saved), the shard's Jinja2 template renders under
    ``StrictUndefined`` and dies on ``{{ params.<new_key> }}`` — a stale
    workflow that never gained the new keys can no longer run.

    Merging manifest defaults into the effective params at dispatch time
    keeps the shard renderable across pack evolution without asking the
    operator to re-save every workflow when a pack adds a knob. The
    ``gnode.params`` still wins on any key it defines, so user overrides
    are preserved verbatim.
    """

    defaults: dict[str, Any] = {}
    if pack_entry is not None:
        for name, spec in (pack_entry.get("params") or {}).items():
            if isinstance(spec, dict) and "default" in spec:
                defaults[name] = spec["default"]
    defaults.update(gnode_params or {})
    return defaults


async def run_snapshot(
    app: FastAPI,
    *,
    snapshot_id: str,
    graph: WorkflowGraph,
    workflow_id: str,
    job_timeout_s: float = 24 * 3600,
    skip_graph_nodes: set[str] | None = None,
    seed_outputs: dict[str, dict[str, str]] | None = None,
) -> list[str]:
    """Execute a snapshot end-to-end. Returns the list of job ids in order.

    Assumes ``graph`` has already passed ``validate_snapshot`` — this function
    trusts every reference. Callers wire up validation in the REST handler.

    ``skip_graph_nodes`` + ``seed_outputs`` power the rerun-from-node
    flow. The endpoint creates ``reused_from_job_id`` rows for the
    upstream steps synchronously before returning, then hands us the
    set of ``graph_node_id`` to skip and their pre-known output handle
    map so downstream ``_wire_inputs`` finds its inputs immediately.
    """

    order = topological_order(graph)
    node_by_id = {n.id: n for n in graph.nodes}

    registry: NodeRegistry = app.state.registry
    store: JobsStore = app.state.jobs_store
    handles: HandleBook = app.state.handles
    hub: FrontendHub = app.state.hub
    snapshot_jobs: SnapshotJobsStore = app.state.snapshot_jobs

    # Lazy pack-catalog lookup — we only need it to check arrayable + per-port
    # arrayed flags for fan-out decisions. Built once per run, keyed by
    # (name, version), pointing at the raw catalog dicts. The catalog only
    # sees currently-connected nodes, which is exactly what snapshot
    # validation already required upstream.
    catalog_by_key: dict[tuple[str, str], dict[str, Any]] = {
        (c["name"], c["version"]): c for c in registry.catalog_json()
    }

    skip = set(skip_graph_nodes or ())
    outputs_by_graph_node: dict[str, dict[str, str]] = dict(seed_outputs or {})
    job_ids: list[str] = []

    for graph_node_id in order:
        if graph_node_id in skip:
            continue

        gnode = node_by_id[graph_node_id]
        try:
            input_handles = _wire_inputs(gnode, graph, outputs_by_graph_node)
        except WorkflowRunError:
            raise

        pack_entry = catalog_by_key.get((gnode.algorithm_name, gnode.algorithm_version))
        needs_fanout = bool(
            gnode.arrayed_toggle and pack_entry is not None and pack_entry.get("arrayable", False)
        )

        if needs_fanout:
            parent_job_id, parent_outputs = await _run_fanout_node(
                app,
                snapshot_id=snapshot_id,
                workflow_id=workflow_id,
                gnode=gnode,
                pack_entry=pack_entry,
                input_handles=input_handles,
                job_timeout_s=job_timeout_s,
            )
            job_ids.append(parent_job_id)
            # Attribution already written at parent-job creation inside
            # ``_prepare_fanout`` (2026-09-22 refactor). The previous
            # post-completion attribute here is redundant under the new
            # invariant "one attribution per job, at creation".
            outputs_by_graph_node[graph_node_id] = parent_outputs
            continue

        job_id = await _dispatch_job(
            registry=registry,
            store=store,
            hub=hub,
            snapshot_id=snapshot_id,
            workflow_id=workflow_id,
            gnode=gnode,
            input_handles=input_handles,
            pack_entry=pack_entry,
            snapshot_jobs=snapshot_jobs,
        )
        job_ids.append(job_id)

        # Wait for terminal state.
        final = await _await_job_terminal(store, job_id, timeout_s=job_timeout_s)
        if final.state is not JobState.DONE:
            raise WorkflowRunError(
                f"job {job_id} for graph node {graph_node_id!r} finished in state "
                f"{final.state.value}"
            )

        # Attribution already written at creation inside ``_dispatch_job``
        # (2026-09-22 refactor). No explicit post-completion attribute
        # needed here — the sequential runner's downstream nodes will see
        # the attributed row + the now-DONE state via ``get_job_at`` +
        # ``jobs_store.get``.
        outputs_by_graph_node[graph_node_id] = await _collect_output_handles(handles, job_id)

    return job_ids


# ---------------------------------------------------------------------------
# arrayed<T> fan-out (M3)
# ---------------------------------------------------------------------------


@dataclass
class _FanoutPlan:
    """Everything needed to run a fan-out — assembled before the first
    shard dispatch so callers can decide to run the body inline (the
    ``run_snapshot`` path) or spawn it as a background task (the
    single-node dispatch path)."""

    parent_job: Job
    parent_ws: str
    element_ids: list[str]
    arrayed_input_ports: list[str]
    pack_outputs: dict[str, dict[str, Any]]
    input_handles: dict[str, str]
    session_node_id: str


async def _prepare_fanout(
    app: FastAPI,
    *,
    snapshot_id: str,
    workflow_id: str,
    gnode: GraphNode,
    pack_entry: dict[str, Any],
    input_handles: dict[str, str],
) -> _FanoutPlan:
    """Validate + create the parent-job row. Returns as soon as the row
    is written (before any shard dispatch), so a fire-and-forget caller
    can hand the parent_job_id back to the client.
    """

    registry: NodeRegistry = app.state.registry
    store: JobsStore = app.state.jobs_store
    handles: HandleBook = app.state.handles
    hub: FrontendHub = app.state.hub

    assert gnode.assigned_node_id is not None
    session = registry.get_session(gnode.assigned_node_id)
    if session is None:
        raise WorkflowRunError(
            f"assigned compute node {gnode.assigned_node_id!r} is no longer connected"
        )
    if session.workspace_root is None:
        raise WorkflowRunError(
            f"compute node {gnode.assigned_node_id!r} has no known workspace_root — "
            "cannot compute shard output prefix for fan-out"
        )

    pack_inputs: dict[str, dict[str, Any]] = pack_entry.get("inputs", {})
    pack_outputs: dict[str, dict[str, Any]] = pack_entry.get("outputs", {})
    arrayable = bool(pack_entry.get("arrayable", False))

    arrayed_input_ports = [
        port
        for port, spec in pack_inputs.items()
        if effective_port_arrayed(
            bool(spec.get("arrayed", False)),
            arrayable,
            True,
            port_scalar=bool(spec.get("scalar", False)),
        )
    ]
    if not arrayed_input_ports:
        raise WorkflowRunError(
            f"graph node {gnode.id!r} has arrayed_toggle on but its pack "
            f"declares no arrayed inputs — nothing to fan out over"
        )

    # Depth per port — from manifest ``dim_labels`` when present, else 1
    # (legacy single-layer arrayed).
    port_depths = {
        port: (len(pack_inputs[port].get("dim_labels") or []) or 1) for port in arrayed_input_ports
    }
    element_ids = await _discover_element_ids(
        handles=handles,
        input_handles=input_handles,
        arrayed_input_ports=arrayed_input_ports,
        port_depths=port_depths,
    )

    effective_params = merged_params_with_defaults(gnode.params, pack_entry)
    parent_job = Job(
        job_id=str(uuid.uuid4()),
        workflow_id=workflow_id,
        snapshot_id=snapshot_id,
        algorithm_name=gnode.algorithm_name,
        algorithm_version=gnode.algorithm_version,
        params=effective_params,
        input_handles=input_handles,
        graph_node_id=gnode.id,
        state=JobState.PENDING,
        node_id=session.node_id,
        # Planned shard count — frozen at the moment fan-out begins so the
        # frontend's progress denominator is the plan, not the lazily-
        # created row-count that grows as shards are dispatched.
        expected_shards=len(element_ids),
    )
    await store.create(parent_job)
    # Attribute at parent-job CREATION, not after the fan-out completes.
    # A downstream ``dispatch_graph_node`` that runs while shards are in
    # flight looks the parent up via ``snapshot_jobs.get_job_at`` and
    # inspects its ``state``; without an early attribution the slot
    # reads as "no produced artifact; run it first" mid-fan-out, which
    # is misleading (2026-09-22 report). ``_resolve_inputs_from_snapshot``
    # now branches on the attributed job's state:
    #   * DONE           → wire the handles
    #   * PENDING/ASSIGNED/RUNNING → "still running (X/Y shards)"
    #   * FAILED/CANCELLED         → "last attempt failed; re-run"
    # Continue-vs-Fork detection (see the ``if base_snapshot_id`` block
    # in ``dispatch_graph_node``) treats FAILED/CANCELLED as slot-empty
    # so a re-run of a failed slot stays a Continue, not a Fork.
    snapshot_jobs_store: SnapshotJobsStore = app.state.snapshot_jobs
    await snapshot_jobs_store.attribute(snapshot_id, parent_job.job_id, gnode.id)
    _push_update(hub, parent_job)

    parent_ws = str(Path(session.workspace_root) / "w" / workflow_id / "j" / parent_job.job_id)
    log.info(
        "fanout begin",
        parent_job_id=parent_job.job_id,
        graph_node=gnode.id,
        pack=f"{gnode.algorithm_name}@{gnode.algorithm_version}",
        element_count=len(element_ids),
        compute_node=session.node_id,
    )
    return _FanoutPlan(
        parent_job=parent_job,
        parent_ws=parent_ws,
        element_ids=element_ids,
        arrayed_input_ports=arrayed_input_ports,
        pack_outputs=pack_outputs,
        input_handles=input_handles,
        session_node_id=session.node_id,
    )


async def _execute_fanout_body(
    app: FastAPI,
    *,
    snapshot_id: str,
    workflow_id: str,
    gnode: GraphNode,
    plan: _FanoutPlan,
    job_timeout_s: float,
) -> dict[str, str]:
    """Run the shard pool for a prepared fan-out. Returns
    ``{port -> aggregate_handle_id}``. Transitions the parent job
    PENDING → ASSIGNED → RUNNING → DONE|FAILED. Raises
    :class:`WorkflowRunError` on any shard failure — after every shard has
    reached a terminal state, so a stragglers-still-running node cannot
    leak past the raise.

    Two phases:

    1. **Row creation** — every shard's ``jobs`` row is written in
       ``PENDING`` upfront (matches ``expected_shards`` semantics, gives
       the snapshot detail endpoint / RecentJobsPanel the full row set
       from the first frame). No ``JobAssign`` frames are sent yet.
    2. **Bounded dispatch** — an ``asyncio.Semaphore(parallelism)`` gates
       how many shards are ASSIGNED + in flight at once. All shards run
       through :func:`asyncio.gather` with ``return_exceptions=True`` so
       one failure does NOT short-circuit the pool (fail-fast v2 concern);
       the parent is marked FAILED once after every shard is terminal.
    """

    registry: NodeRegistry = app.state.registry
    store: JobsStore = app.state.jobs_store
    handles: HandleBook = app.state.handles
    hub: FrontendHub = app.state.hub

    parent_assigned = JobStateMachine.transition(
        plan.parent_job, JobState.ASSIGNED, node_id=plan.session_node_id
    )
    _, payload = event_from_transition(plan.parent_job, parent_assigned)
    await store.update(parent_assigned, "transition:assigned", payload)
    _push_update(hub, parent_assigned)
    parent_running = JobStateMachine.transition(parent_assigned, JobState.RUNNING)
    _, payload = event_from_transition(parent_assigned, parent_running)
    await store.update(parent_running, "transition:running", payload)
    _push_update(hub, parent_running)

    # -- Phase 1: create every shard row upfront in PENDING ------------------
    #
    # Serial (~48 s on 100-shard merge fan-out): 3 handle reads + 3 handle
    # writes + 1 job write + 1 event write per shard, each ``Database.write``
    # trip its own transaction. That's ~800 fsyncs interleaved with unrelated
    # writer-queue traffic. Batched (below): O(1) parent-handle reads +
    # one ``register_many`` for every sub-handle + one ``create_many`` for
    # every shard row. Two transactions total instead of hundreds.
    shards: list[tuple[int, str, Job, dict[str, str]]] = await _prepare_shard_rows(
        store=store,
        handles=handles,
        plan=plan,
        snapshot_id=snapshot_id,
        workflow_id=workflow_id,
        gnode=gnode,
    )
    # Non-blocking WS broadcast for every freshly-minted PENDING row.
    # Each ``_push_update`` is a per-subscriber ``queue.put_nowait`` — the
    # actual send happens on the subscriber's pump task, so this loop is
    # a tight in-memory hot path, not another DB round-trip storm.
    for _idx, _eid, shard, _paths in shards:
        _push_update(hub, shard)

    # -- Phase 2: bounded-concurrent dispatch --------------------------------
    parallelism = max(1, int(getattr(gnode, "parallelism", 1) or 1))
    sem = asyncio.Semaphore(parallelism)

    async def _run_one(
        idx: int, element_id: str, shard: Job, shard_input_paths: dict[str, str]
    ) -> tuple[int, str, Job]:
        async with sem:
            # Between phase 1 (shard created PENDING) and now, the parent
            # or this shard may have been cancelled by an API call. Read
            # both back from the DB before dispatching — otherwise a
            # stale in-memory ``shard`` (still PENDING) would drive a
            # PENDING→ASSIGNED transition that overwrites the CANCELLED
            # state the cancel API just wrote. That was the "cancel-all
            # doesn't stop the loop, new colmap keeps spawning" bug.
            shard_now = await store.get(shard.job_id)
            if shard_now is None:
                return (idx, element_id, shard)
            if shard_now.state != JobState.PENDING:
                # Cascade already flipped this row (or somebody cancelled
                # this shard directly). Do not dispatch.
                return (idx, element_id, shard_now)
            parent_now = await store.get(plan.parent_job.job_id)
            if parent_now is not None and parent_now.state == JobState.CANCELLED:
                # Parent was cancelled during dispatch. Flip this pending
                # shard to CANCELLED so downstream aggregation and the
                # snapshot detail endpoint see a coherent terminal state
                # rather than a permanent PENDING row.
                if JobStateMachine.can_transition(shard_now.state, JobState.CANCELLED):
                    cancelled = JobStateMachine.transition(
                        shard_now,
                        JobState.CANCELLED,
                        fail_reason=JobFailReason.CANCELLED,
                    )
                    _, payload = event_from_transition(shard_now, cancelled)
                    await store.update(cancelled, "transition:cancelled", payload)
                    _push_update(hub, cancelled)
                    return (idx, element_id, cancelled)
                return (idx, element_id, shard_now)
            try:
                await _dispatch_prepared_shard(
                    registry=registry,
                    store=store,
                    hub=hub,
                    gnode=gnode,
                    shard=shard_now,
                    shard_element_id=element_id,
                    shard_output_prefix=plan.parent_ws,
                    shard_input_paths=shard_input_paths,
                )
            except Exception as exc:
                # Log before re-raising into ``gather(return_exceptions=True)``
                # so the failure isn't silently swallowed. The aggregator
                # still records this shard as ``shard raised: ...`` in the
                # parent's fail_message; this log makes triage easy without
                # crawling the parent row.
                log.warning(
                    "fanout shard dispatch failed",
                    parent_job_id=plan.parent_job.job_id,
                    shard_job_id=shard.job_id,
                    element_id=element_id,
                    error=str(exc),
                )
                # If the shard never made it past PENDING (e.g. session was
                # gone by the time ``_dispatch_prepared_shard`` looked it
                # up), flip it here. Otherwise the row stays ``pending``
                # forever: the mid-flight orphan sweep only covers
                # ``assigned``/``running`` (see
                # ``registry.mark_stuck_jobs_orphaned_for_node``), and the
                # fanout aggregator's ``_mark_parent_failed`` never touches
                # shard rows. Shards that failed AFTER the ASSIGNED
                # transition are already handled inside
                # ``_dispatch_prepared_shard`` via ``_fail_after_send_error``.
                current = await store.get(shard.job_id)
                if current is not None and current.state is JobState.PENDING:
                    failed = JobStateMachine.transition(
                        current,
                        JobState.FAILED,
                        fail_reason=JobFailReason.SYSTEM_ERROR,
                        fail_message=f"dispatch failed: {exc!r}",
                    )
                    _, payload = event_from_transition(current, failed)
                    await store.update(failed, "transition:failed", payload)
                    _push_update(hub, failed)
                raise
            final = await _await_job_terminal(store, shard.job_id, timeout_s=job_timeout_s)
            return (idx, element_id, final)

    log.info(
        "fanout dispatch pool",
        parent_job_id=plan.parent_job.job_id,
        graph_node=gnode.id,
        shard_count=len(shards),
        parallelism=parallelism,
    )
    results = await asyncio.gather(
        *(_run_one(i, eid, s, paths) for (i, eid, s, paths) in shards),
        return_exceptions=True,
    )

    # If the parent was cancelled by an API call while dispatch was in
    # flight, the parent row is already CANCELLED in the DB. Do NOT try
    # to flip it to DONE or FAILED — that would (a) illegally re-open a
    # terminal state or (b) overwrite the operator's cancel decision.
    # Still raise so ``run_snapshot`` halts downstream nodes rather than
    # feeding them a partial aggregate.
    parent_check = await store.get(plan.parent_job.job_id)
    if parent_check is not None and parent_check.state is JobState.CANCELLED:
        raise WorkflowRunError(f"fanout for graph node {gnode.id!r}: cancelled by operator")

    # Collect every failure before marking the parent — we do not fail-fast
    # (v1 semantics per product spec). Truncate the reason to keep the DB
    # column bounded when a very wide fan-out fails wholesale.
    # ``CANCELLED`` shards from an operator cancel are not counted as
    # shard-authored failures; the parent-check above short-circuits that
    # case, so any remaining CANCELLED here means a per-shard cancel that
    # the operator wanted logged rather than eaten.
    failures: list[str] = []
    shard_fail_reasons: list[JobFailReason] = []
    for r in results:
        if isinstance(r, BaseException):
            failures.append(f"shard raised: {r!r}")
            shard_fail_reasons.append(JobFailReason.SYSTEM_ERROR)
            continue
        idx, element_id, final = r
        if final.state is not JobState.DONE:
            msg = f"shard {idx} (element {element_id!r}) finished {final.state.value}"
            if final.fail_message:
                msg += f": {final.fail_message}"
            failures.append(msg)
            if final.fail_reason is not None:
                shard_fail_reasons.append(final.fail_reason)
    if failures:
        reason = "; ".join(failures[:5])
        if len(failures) > 5:
            reason += f"; +{len(failures) - 5} more"
        # Preserve infra-class classification on the parent when every
        # shard-authored failure was transport-class (gateway unreachable,
        # peer 5xx, node crash). Mixed or user-authored failures fall back
        # to USER_ERROR — the default aggregate reason. This keeps a
        # briefly-starved gateway from painting a whole fan-out as
        # "user error" in the run history + retry policy.
        parent_reason = _aggregate_parent_fail_reason(shard_fail_reasons)
        await _mark_parent_failed(
            store, hub, parent_running, reason=reason, fail_reason=parent_reason
        )
        raise WorkflowRunError(f"fanout for graph node {gnode.id!r}: {reason}")

    # Resolve ``tags_from`` for the aggregate handles the same way
    # ``handle_register`` does for scalar outputs — otherwise a generic
    # utility pack's arrayed aggregate (e.g. ``regroup.out``) lands in
    # the handle book as ``["any"]`` and hides its runtime class from
    # tag-driven consumers. One catalog snapshot is enough for all ports.
    catalog_json = registry.catalog_json()
    packs_by_key = packs_by_key_from_catalog(catalog_json)

    parent_outputs: dict[str, str] = {}
    for port_name, spec in plan.pack_outputs.items():
        aggregate_path = str(Path(plan.parent_ws) / port_name)
        try:
            size_bytes = _dir_size_bytes(Path(aggregate_path))
        except OSError:
            size_bytes = None
        resolved_tags = await resolve_handle_output_tags(
            app.state.workflows,
            packs_by_key,
            snapshot_id=snapshot_id,
            workflow_id=workflow_id,
            graph_node_id=gnode.id,
            port_name=port_name,
            raw_tags=list(spec.get("tags", [])),
        )
        aggregate = Handle(
            handle_id=str(uuid.uuid4()),
            node_id=plan.session_node_id,
            storage=spec.get("storage", "dir"),
            tags=resolved_tags,
            path=aggregate_path,
            size_bytes=size_bytes,
            job_id=plan.parent_job.job_id,
            output_port_name=port_name,
        )
        await handles.register(aggregate)
        parent_outputs[port_name] = aggregate.handle_id

    parent_done = JobStateMachine.transition(parent_running, JobState.DONE)
    _, payload = event_from_transition(parent_running, parent_done)
    await store.update(parent_done, "transition:done", payload)
    _push_update(hub, parent_done)
    log.info(
        "fanout done",
        parent_job_id=plan.parent_job.job_id,
        graph_node=gnode.id,
        element_count=len(plan.element_ids),
    )
    return parent_outputs


async def _prepare_shard_rows(
    *,
    store: JobsStore,
    handles: HandleBook,
    plan: _FanoutPlan,
    snapshot_id: str,
    workflow_id: str,
    gnode: GraphNode,
) -> list[tuple[int, str, Job, dict[str, str]]]:
    """Materialise every shard's ``jobs`` row + synthetic sub-handles in-memory,
    then flush them to SQLite in two batched transactions.

    Semantics are byte-for-byte equivalent to the previous per-shard loop
    (:func:`_shard_input_handles` + :func:`_create_shard_row` called N times).
    The batching only touches persistence: sub-handles inherit the parent's
    tags / storage / node_id and carry ``job_id=None`` exactly as before,
    and shard rows carry the same PENDING state + input_handles map + params.

    Why the split matters:

    * Old path: each shard triggered 3+ ``Database.write`` round-trips
      (1 read per arrayed input to fetch the parent handle, 1 write per
      arrayed input to register the sub-handle, plus 1 write for the
      shard row + its ``job_events`` "created" entry). For a 100-shard,
      3-arrayed-input fan-out that was ~700 writer-loop trips, each its
      own transaction + fsync. Because the writer coroutine interleaves
      with unrelated gateway work, the observed phase-1 wall was ~48 s.
    * New path: read each parent handle **once** (K reads for K arrayed
      ports, not K x N), then ``register_many`` all N x K sub-handles in
      one transaction and ``create_many`` all N shard rows in another.
      Two commits instead of ~700; the observed wall drops to <500 ms.

    Broadcast is deliberately NOT batched here — the caller emits
    per-shard ``job_update`` frames in a tight non-blocking loop after
    this function returns, so the frontend's wire contract (one frame
    per shard, in the order shards were created) stays intact.
    """

    if not plan.element_ids:
        return []

    # 1) One read per arrayed input port. The scalar (non-arrayed) input
    #    handles pass through unchanged and never need a lookup here.
    parent_by_port: dict[str, Handle] = {}
    for port in plan.arrayed_input_ports:
        parent_handle_id = plan.input_handles[port]
        parent = await handles.get(parent_handle_id)
        if parent is None:
            raise WorkflowRunError(
                f"arrayed input handle {parent_handle_id!r} for port {port!r} not registered"
            )
        parent_by_port[port] = parent

    # 2) Build every sub-handle + shard Job in memory, no DB writes yet.
    #
    # Also stash the concrete local path for every arrayed sub-handle in
    # ``shard_input_paths`` — the gateway already knows the answer
    # (``parent.path / element_id``), and passing it inline on the
    # ``JobAssign`` frame lets the node skip a per-shard
    # ``handle_locate`` round-trip against the gateway. See
    # ``JobAssign.input_paths`` in ``protocol/messages.py``.
    sub_handles: list[Handle] = []
    shard_rows: list[Job] = []
    shards: list[tuple[int, str, Job, dict[str, str]]] = []
    now = time.time()
    for idx, element_id in enumerate(plan.element_ids):
        shard_inputs: dict[str, str] = {}
        shard_input_paths: dict[str, str] = {}
        for port, handle_id in plan.input_handles.items():
            if port not in plan.arrayed_input_ports:
                shard_inputs[port] = handle_id
                continue
            parent = parent_by_port[port]
            sub_path = str(Path(parent.path) / element_id)
            sub = Handle(
                handle_id=str(uuid.uuid4()),
                node_id=plan.session_node_id,
                storage=parent.storage,
                tags=list(parent.tags),
                path=sub_path,
                size_bytes=None,
                job_id=None,
                output_port_name=None,
                created_ts=now,
            )
            sub_handles.append(sub)
            shard_inputs[port] = sub.handle_id
            shard_input_paths[port] = sub_path

        shard = Job(
            job_id=str(uuid.uuid4()),
            workflow_id=workflow_id,
            snapshot_id=snapshot_id,
            algorithm_name=gnode.algorithm_name,
            algorithm_version=gnode.algorithm_version,
            params=dict(plan.parent_job.params),
            input_handles=shard_inputs,
            graph_node_id=gnode.id,
            parent_job_id=plan.parent_job.job_id,
            shard_element_id=element_id,
            created_ts=now,
            updated_ts=now,
        )
        shard_rows.append(shard)
        shards.append((idx, element_id, shard, shard_input_paths))

    # 3) Two flushes. Handles first so that when a peer reader observes
    #    a shard's input_handles map, every referenced handle_id already
    #    resolves — the visibility rule matches the pre-refactor order
    #    ("register then create-row").
    await handles.register_many(sub_handles)
    await store.create_many(shard_rows)
    return shards


async def _run_fanout_node(
    app: FastAPI,
    *,
    snapshot_id: str,
    workflow_id: str,
    gnode: GraphNode,
    pack_entry: dict[str, Any],
    input_handles: dict[str, str],
    job_timeout_s: float,
) -> tuple[str, dict[str, str]]:
    """Blocking fan-out — used by :func:`run_snapshot` where the caller
    waits for every shard to finish before proceeding to the next node.

    See :func:`_prepare_fanout` + :func:`_execute_fanout_body` for the
    split used by the single-node dispatch path.
    """

    plan = await _prepare_fanout(
        app,
        snapshot_id=snapshot_id,
        workflow_id=workflow_id,
        gnode=gnode,
        pack_entry=pack_entry,
        input_handles=input_handles,
    )
    outputs = await _execute_fanout_body(
        app,
        snapshot_id=snapshot_id,
        workflow_id=workflow_id,
        gnode=gnode,
        plan=plan,
        job_timeout_s=job_timeout_s,
    )
    return plan.parent_job.job_id, outputs


def _list_subdirs(path: str, *, port: str) -> list[str]:
    """List sorted immediate subdir names, excluding dotfiles."""
    try:
        with os.scandir(path) as it:
            return sorted(
                e.name for e in it if not e.name.startswith(".") and e.is_dir(follow_symlinks=True)
            )
    except OSError as exc:
        raise WorkflowRunError(f"cannot list arrayed input {port!r} ({path}): {exc}") from exc


def _enumerate_depth(root: str, depth: int, *, port: str) -> list[str]:
    """Recurse ``depth`` levels below ``root``; return joined element paths.

    Depth 1 → ``["cam_A", ...]`` (legacy 1-D). Depth 2 →
    ``["frame_0000/camera_0000", ...]`` — outer first, matching
    ``dim_labels``. Empty inner set drops silently; cross-port set
    comparison catches the asymmetry.
    """
    if depth <= 0:
        return [""]
    if depth == 1:
        return _list_subdirs(root, port=port)
    outer = _list_subdirs(root, port=port)
    joined: list[str] = []
    for name in outer:
        for sub in _enumerate_depth(os.path.join(root, name), depth - 1, port=port):
            joined.append(f"{name}/{sub}" if sub else name)
    return joined


async def _discover_element_ids(
    *,
    handles: HandleBook,
    input_handles: dict[str, str],
    arrayed_input_ports: list[str],
    port_depths: dict[str, int] | None = None,
) -> list[str]:
    """Return the sorted flat element-path list. Rejects mismatched sets.

    Element candidates = **subdirectory entries only, excluding dotfiles**.
    Hidden files the framework itself writes (``.hololab-done`` idempotency
    marker, ``.hololab-metadata.json``, etc.) live alongside real elements
    in a producer's output dir but must not be dispatched as shards.
    Files that aren't directories are also excluded — an arrayed<T> element
    is by definition a subdir (the T's on-disk form is a directory).

    ``port_depths`` maps each arrayed port to its effective arrayed depth
    (``len(dim_labels)`` or 1 when arrayed with no labels). Omitted →
    every port at depth 1 (pre-N-D behavior). For depth > 1, each
    element is the ``/``-joined path from the port's handle root down to
    a leaf subdir, outer first. Cross-port depth mismatch is rejected
    upfront because zipping element sets of different shapes is undefined.
    """
    depths = dict(port_depths or {})
    for port in arrayed_input_ports:
        depths.setdefault(port, 1)
    distinct_depths = {depths[p] for p in arrayed_input_ports}
    if len(distinct_depths) > 1:
        raise WorkflowRunError(
            "arrayed inputs disagree on depth: "
            + ", ".join(f"{p!r}=depth {depths[p]}" for p in arrayed_input_ports)
            + " — cannot zip element sets of different shapes"
        )

    sets_by_port: dict[str, list[str]] = {}
    for port in arrayed_input_ports:
        handle_id = input_handles.get(port)
        if handle_id is None:
            raise WorkflowRunError(
                f"arrayed input {port!r} is not wired — cannot enumerate elements"
            )
        h = await handles.get(handle_id)
        if h is None:
            raise WorkflowRunError(
                f"arrayed input handle {handle_id!r} for port {port!r} is not registered"
            )
        sets_by_port[port] = _enumerate_depth(h.path, depths[port], port=port)

    reference_port, reference = next(iter(sets_by_port.items()))
    for port, entries in sets_by_port.items():
        if entries != reference:
            raise WorkflowRunError(
                "arrayed inputs disagree on element set: "
                + _format_element_set_mismatch(reference_port, reference, port, entries)
            )
    return reference


def _format_element_set_mismatch(
    ref_port: str, ref: list[str], other_port: str, other: list[str], *, cap: int = 5
) -> str:
    """Human-scannable diff — leads with cardinality, then first-N symmetric diff.

    A cams=100 vs images=99 mismatch dumping 199 names each side is unreadable,
    so we surface (a) both counts, (b) the first `cap` names present on one
    side but not the other, plus a "(+N more)" tail when the diff overflows.
    """
    ref_set, other_set = set(ref), set(other)
    only_ref = sorted(ref_set - other_set)
    only_other = sorted(other_set - ref_set)
    parts = [
        f"{ref_port!r} has {len(ref)} element(s)",
        f"{other_port!r} has {len(other)} element(s)",
    ]
    if only_ref:
        head = ", ".join(repr(x) for x in only_ref[:cap])
        tail = f" (+{len(only_ref) - cap} more)" if len(only_ref) > cap else ""
        parts.append(f"only in {ref_port!r}: [{head}{tail}]")
    if only_other:
        head = ", ".join(repr(x) for x in only_other[:cap])
        tail = f" (+{len(only_other) - cap} more)" if len(only_other) > cap else ""
        parts.append(f"only in {other_port!r}: [{head}{tail}]")
    return "; ".join(parts)


async def _shard_input_handles(
    *,
    handles: HandleBook,
    input_handles: dict[str, str],
    arrayed_input_ports: list[str],
    element_id: str,
    producer_node_id: str,
) -> dict[str, str]:
    """Build one shard's ``input_handles`` map.

    For each arrayed input port we register a *synthetic sub-handle* whose
    path is the parent handle's path joined with the element_id. Same-node
    handle_locate short-circuits to the local path, so no cross-node
    transport is needed. Non-arrayed inputs are passed through unchanged.

    The synthetic handle rows are transient bookkeeping — they inherit tags
    and storage from the parent handle. They are not registered against any
    job (``job_id`` NULL) so they don't pollute per-job artifact accounting.
    """

    shard_inputs: dict[str, str] = {}
    for port, handle_id in input_handles.items():
        if port not in arrayed_input_ports:
            shard_inputs[port] = handle_id
            continue
        parent_handle = await handles.get(handle_id)
        if parent_handle is None:
            raise WorkflowRunError(
                f"arrayed input handle {handle_id!r} for port {port!r} not registered"
            )
        sub_path = str(Path(parent_handle.path) / element_id)
        sub_handle = Handle(
            handle_id=str(uuid.uuid4()),
            node_id=producer_node_id,
            storage=parent_handle.storage,
            tags=list(parent_handle.tags),
            path=sub_path,
            size_bytes=None,
            job_id=None,
            output_port_name=None,
        )
        await handles.register(sub_handle)
        shard_inputs[port] = sub_handle.handle_id
    return shard_inputs


async def _create_shard_row(
    *,
    store: JobsStore,
    hub: FrontendHub,
    snapshot_id: str,
    workflow_id: str,
    gnode: GraphNode,
    parent_job_id: str,
    shard_element_id: str,
    shard_input_handles: dict[str, str],
    params: dict[str, Any],
) -> Job:
    """Create one shard job row in ``PENDING``. No transition, no send.

    Split out from :func:`_dispatch_prepared_shard` so a bounded-concurrent
    fan-out can materialise every shard row upfront (giving the snapshot
    detail endpoint / RecentJobsPanel the full row set from t=0) and then
    stagger the ASSIGNED transitions through a semaphore.

    ``params`` is the parent job's already-merged effective params — see
    :func:`merged_params_with_defaults`. Passed explicitly so every shard
    inherits the same rendered params as its parent (bypasses the stale-
    workflow gap where ``gnode.params`` still lacks manifest defaults).
    """

    shard = Job(
        job_id=str(uuid.uuid4()),
        workflow_id=workflow_id,
        snapshot_id=snapshot_id,
        algorithm_name=gnode.algorithm_name,
        algorithm_version=gnode.algorithm_version,
        params=dict(params),
        input_handles=shard_input_handles,
        graph_node_id=gnode.id,
        parent_job_id=parent_job_id,
        shard_element_id=shard_element_id,
    )
    await store.create(shard)
    _push_update(hub, shard)
    return shard


async def _fail_after_send_error(
    *,
    store: JobsStore,
    hub: FrontendHub,
    job: Job,
    exc: BaseException,
    transition_tag: str,
) -> None:
    """Finalise a just-ASSIGNED row as FAILED when ``send_text`` blew up.

    Cleanup path for the dispatch helpers: the row was already written
    in ASSIGNED before we tried to hand the frame to the WS. When that
    send raises (dead socket, keepalive timeout, transient close), the
    node has not learned about the job — so we must not leave a row
    the node has no way to complete. Flip to FAILED with SYSTEM_ERROR
    (infra-class, propagates to the parent's aggregate reason via
    :func:`_aggregate_parent_fail_reason`) and broadcast the update.
    Any error inside this cleanup is logged and swallowed so it never
    masks the original send failure — the caller re-raises that.
    """

    try:
        failed = JobStateMachine.transition(
            job,
            JobState.FAILED,
            fail_reason=JobFailReason.SYSTEM_ERROR,
            fail_message=f"send failed: {exc!r}",
        )
        _, payload = event_from_transition(job, failed)
        await store.update(failed, transition_tag, payload)
        _push_update(hub, failed)
    except Exception as cleanup_exc:  # pragma: no cover — defensive
        log.warning(
            "dispatch send-failure cleanup failed",
            job_id=job.job_id,
            original_error=str(exc),
            cleanup_error=str(cleanup_exc),
        )


async def _dispatch_prepared_shard(
    *,
    registry: NodeRegistry,
    store: JobsStore,
    hub: FrontendHub,
    gnode: GraphNode,
    shard: Job,
    shard_element_id: str,
    shard_output_prefix: str,
    shard_input_paths: dict[str, str] | None = None,
) -> None:
    """Transition an already-created shard PENDING → ASSIGNED and send its
    :class:`JobAssign` frame to the compute node. The compute-node session
    is re-looked-up here (not passed in) so a session drop mid-fanout
    surfaces as a per-shard failure rather than a wholesale crash.

    ``shard_input_paths`` is an optional ``{port -> absolute_local_path}``
    map for inputs whose location the gateway already knows (arrayed
    sub-handles register at ``parent.path / element_id`` — deterministic).
    Passed inline on the ``JobAssign`` frame so the node can skip the
    per-shard ``handle_locate`` round-trip for those ports. Ports omitted
    from this map fall through to the usual locate path on the node.
    """

    assert gnode.assigned_node_id is not None
    session = registry.get_session(gnode.assigned_node_id)
    if session is None:
        raise WorkflowRunError(
            f"assigned compute node {gnode.assigned_node_id!r} dropped mid-fanout"
        )

    assigned = JobStateMachine.transition(shard, JobState.ASSIGNED, node_id=session.node_id)
    kind, payload = event_from_transition(shard, assigned)
    await store.update(assigned, kind, payload)
    _push_update(hub, assigned)

    assign_msg = JobAssign(
        job_id=assigned.job_id,
        workflow_id=assigned.workflow_id,
        algorithm_name=assigned.algorithm_name,
        algorithm_version=assigned.algorithm_version,
        params=assigned.params,
        input_handles=assigned.input_handles,
        graph_node_id=assigned.graph_node_id,
        shard_element_id=shard_element_id,
        shard_output_prefix=shard_output_prefix,
        input_paths=dict(shard_input_paths) if shard_input_paths else {},
    )
    frame = encode("job_assign", assign_msg, v=session.protocol_v)
    try:
        async with session.send_lock:
            await session.ws.send_text(frame)
    except Exception as exc:
        # The state transition to ASSIGNED above already landed in the
        # DB; if we let this raise without cleanup the row would stay
        # ``assigned`` forever (the node never received the frame, so
        # it will never send a terminal ``job_done`` / ``job_fail`` for
        # it). Flip to FAILED with SYSTEM_ERROR so the fanout aggregator
        # counts it as an infra-class failure and the parent job can
        # reach a terminal state promptly instead of waiting for
        # ``job_timeout_s`` (~24 h) to fire.
        await _fail_after_send_error(
            store=store,
            hub=hub,
            job=assigned,
            exc=exc,
            transition_tag="transition:failed",
        )
        raise

    log.info(
        "shard dispatched",
        parent_job_id=shard.parent_job_id,
        shard_job_id=assigned.job_id,
        element_id=shard_element_id,
    )


async def _mark_parent_failed(
    store: JobsStore,
    hub: FrontendHub,
    parent_running: Job,
    *,
    reason: str,
    fail_reason: JobFailReason = JobFailReason.USER_ERROR,
) -> None:
    """Transition a parent job to FAILED when a shard fails."""

    parent_failed = JobStateMachine.transition(
        parent_running,
        JobState.FAILED,
        fail_reason=fail_reason,
        fail_message=reason,
    )
    _, payload = event_from_transition(parent_running, parent_failed)
    await store.update(parent_failed, "transition:failed", payload)
    _push_update(hub, parent_failed)


def _aggregate_parent_fail_reason(shard_reasons: list[JobFailReason]) -> JobFailReason:
    """Choose the parent's ``fail_reason`` from the shard-level reasons.

    Rule: if every observed shard failure was infrastructure-class
    (``SYSTEM_ERROR`` or ``OOM``), the parent inherits ``SYSTEM_ERROR``
    — the workflow itself wasn't wrong, the plumbing was. Any user- or
    algorithm-authored failure downgrades the aggregate to
    ``USER_ERROR`` (the historical default) so operators aren't misled
    into treating a real bad-input case as transient. An empty list
    (shouldn't happen — we only call this when ``failures`` is
    non-empty) also falls back to ``USER_ERROR``.
    """

    if not shard_reasons:
        return JobFailReason.USER_ERROR
    infra = {JobFailReason.SYSTEM_ERROR, JobFailReason.OOM}
    if all(r in infra for r in shard_reasons):
        return JobFailReason.SYSTEM_ERROR
    return JobFailReason.USER_ERROR


def _dir_size_bytes(path: Path) -> int | None:
    """Best-effort directory-size walker for the aggregate handle's registration."""

    if not path.exists():
        return None
    total = 0
    try:
        for root, _dirs, files in os.walk(path, followlinks=False):
            for name in files:
                with contextlib.suppress(OSError):
                    total += Path(root, name).stat().st_size
    except OSError:
        return None
    return total


async def _resolve_origin_job_id(store: JobsStore, job: dict) -> str:
    """Walk the ``reused_from_job_id`` chain and return the origin job id.

    Handle registrations live only on jobs that actually executed a shell
    — reused-row jobs (rerun-from bookkeeping) inherit outputs by
    pointing at the job they reused. When rerun-from consumes a snapshot
    whose upstream was ITSELF a rerun-from, the graph is::

        Snap3.reused_row -> Snap2.reused_row -> Snap1.executed_job

    Direct ``handles.list_by_job(Snap2.reused_row.job_id)`` returns
    nothing; we must walk to ``Snap1.executed_job.job_id``. New code
    flattens the chain on write (see the same-named helper in
    ``gateway.app``), but any historical rows built before that flatten
    could still be N hops deep, so we bounded-loop with a cap to avoid
    an infinite loop on pathological data.

    Fan-out safety: when the input row is a SHARD (``parent_job_id`` set),
    hop to its parent first. The parent owns the aggregate arrayed<T>
    handle downstream needs; a shard only owns its per-element slice.
    This heals corrupt snapshots that had only shard attributions in
    ``snapshot_jobs`` (from pre-fix rerun-from selections) — the caller
    then hands the parent's job_id to ``handles.list_by_job`` and gets
    the aggregate handle back.
    """

    parent_job_id = job.get("parent_job_id")
    if parent_job_id is not None:
        parent_row = await store.get(parent_job_id)
        if parent_row is not None:
            job = _job_to_dict_for_chain(parent_row)

    origin_id = job["job_id"]
    reused = job.get("reused_from_job_id")
    hops = 0
    while reused is not None and hops < 16:
        origin_id = reused
        parent = await store.get(reused)
        if parent is None:
            break
        parent_dict = _job_to_dict_for_chain(parent)
        reused = parent_dict.get("reused_from_job_id")
        hops += 1
    return origin_id


def _job_to_dict_for_chain(job: Any) -> dict:
    """Extract just the fields the chain walker needs, from either a
    ``Job`` dataclass or a plain dict (list_by_snapshot returns dicts,
    store.get returns the dataclass)."""

    if hasattr(job, "job_id"):
        return {
            "job_id": job.job_id,
            "reused_from_job_id": job.reused_from_job_id,
        }
    return {
        "job_id": job.get("job_id"),
        "reused_from_job_id": job.get("reused_from_job_id"),
    }


async def create_reused_job_row(
    *,
    store: JobsStore,
    hub: FrontendHub,
    snapshot_id: str,
    workflow_id: str,
    gnode: GraphNode,
    reused_from_job_id: str,
) -> str:
    """Insert a done Job row for a rerun-from reused upstream, no dispatch.

    Called by the rerun-from endpoint before spawning the background
    executor so the reused rows are visible in ``/api/snapshots/{id}``
    immediately after the POST returns. The row's outputs are inherited
    via ``reused_from_job_id`` (list_by_snapshot walks back to the
    original job's handles).
    """

    job = Job(
        job_id=str(uuid.uuid4()),
        workflow_id=workflow_id,
        snapshot_id=snapshot_id,
        algorithm_name=gnode.algorithm_name,
        algorithm_version=gnode.algorithm_version,
        params=dict(gnode.params),
        input_handles={},
        graph_node_id=gnode.id,
        state=JobState.DONE,
        reused_from_job_id=reused_from_job_id,
        node_id=gnode.assigned_node_id,
    )
    await store.create(job)
    _push_update(hub, job)
    return job.job_id


def _wire_inputs(
    gnode: GraphNode,
    graph: WorkflowGraph,
    outputs_by_graph_node: dict[str, dict[str, str]],
) -> dict[str, str]:
    """Build ``{input_port -> handle_id}`` for one node from its incoming edges."""

    wired: dict[str, str] = {}
    for edge in graph.edges:
        if edge.target != gnode.id:
            continue
        upstream_outputs = outputs_by_graph_node.get(edge.source)
        if upstream_outputs is None:
            raise WorkflowRunError(
                f"internal: upstream {edge.source!r} finished without registered outputs"
            )
        handle_id = upstream_outputs.get(edge.sourceHandle)
        if handle_id is None:
            raise WorkflowRunError(
                f"upstream job produced no output for port {edge.sourceHandle!r}"
            )
        wired[edge.targetHandle] = handle_id
    return wired


async def _dispatch_job(
    *,
    registry: NodeRegistry,
    store: JobsStore,
    hub: FrontendHub,
    snapshot_id: str,
    workflow_id: str,
    gnode: GraphNode,
    input_handles: dict[str, str],
    pack_entry: dict[str, Any] | None = None,
    snapshot_jobs: SnapshotJobsStore | None = None,
) -> str:
    """Create + assign + send job_assign. Returns the newly minted job id.

    ``pack_entry`` is the catalog entry for ``gnode``'s pack; when
    provided, manifest ``params:`` defaults are merged in for any keys
    the workflow draft doesn't set (:func:`merged_params_with_defaults`).

    ``snapshot_jobs`` is required when the caller wants attribute-at-
    creation semantics (2026-09-22 refactor): the freshly-minted job's
    row is written to ``snapshot_jobs`` immediately after ``store.create``
    so a downstream dispatch on the same snapshot sees a filled slot
    even mid-run (previously attribution only landed on WS ``job_done``,
    which opened a race window that surfaced as "no produced artifact"
    to the operator). Callers that don't want attribution (ad-hoc jobs
    with ``snapshot_id=None``) omit it. See ``_prepare_fanout`` for the
    fan-out counterpart.
    """

    assert gnode.assigned_node_id is not None
    session = registry.get_session(gnode.assigned_node_id)
    if session is None:
        raise WorkflowRunError(
            f"assigned compute node {gnode.assigned_node_id!r} is no longer connected"
        )

    job = Job(
        job_id=str(uuid.uuid4()),
        workflow_id=workflow_id,
        snapshot_id=snapshot_id,
        algorithm_name=gnode.algorithm_name,
        algorithm_version=gnode.algorithm_version,
        params=merged_params_with_defaults(gnode.params, pack_entry),
        input_handles=input_handles,
        graph_node_id=gnode.id,
    )
    await store.create(job)
    # Attribute at creation, not at DONE (see the block comment on the
    # ``snapshot_jobs`` parameter above). Skipped when the caller didn't
    # pass a store — ad-hoc jobs typically don't belong to a snapshot.
    if snapshot_jobs is not None:
        await snapshot_jobs.attribute(snapshot_id, job.job_id, gnode.id)
    _push_update(hub, job)

    assigned = JobStateMachine.transition(job, JobState.ASSIGNED, node_id=session.node_id)
    kind, payload = event_from_transition(job, assigned)
    await store.update(assigned, kind, payload)
    _push_update(hub, assigned)

    assign_msg = JobAssign(
        job_id=assigned.job_id,
        workflow_id=assigned.workflow_id,
        algorithm_name=assigned.algorithm_name,
        algorithm_version=assigned.algorithm_version,
        params=assigned.params,
        input_handles=assigned.input_handles,
        graph_node_id=assigned.graph_node_id,
    )
    frame = encode("job_assign", assign_msg, v=session.protocol_v)
    try:
        async with session.send_lock:
            await session.ws.send_text(frame)
    except Exception as exc:
        # See the matching comment in ``_dispatch_prepared_shard`` — the
        # ASSIGNED row is already persisted; if send fails we must
        # finalise it here or the run loop's ``_await_job_terminal``
        # will block on a row the node never learned about.
        await _fail_after_send_error(
            store=store,
            hub=hub,
            job=assigned,
            exc=exc,
            transition_tag="transition:failed",
        )
        raise

    log.info(
        "dispatched",
        job_id=assigned.job_id,
        graph_node=gnode.id,
        pack=f"{gnode.algorithm_name}@{gnode.algorithm_version}",
        compute_node=session.node_id,
    )
    return assigned.job_id


async def _await_job_terminal(
    store: JobsStore, job_id: str, *, timeout_s: float, poll_s: float = 0.5
) -> Job:
    """Poll ``jobs`` until the given job reaches done / failed / cancelled.

    Polling is cheap (indexed by primary key) and lets us stay agnostic to
    how progress arrives (WS from the node, an internal state change, etc.).
    A future refactor could subscribe to an in-process event bus instead.
    """

    # ``INTERRUPTED`` is terminal per the state machine (see
    # ``hololab.gateway.jobs`` — no outbound edges) and is the natural
    # landing state for a shard whose owning node reconnected but
    # doesn't claim it anymore. Without INTERRUPTED here the reconcile
    # would flip the row but the fanout worker would still spin against
    # ``job_timeout_s`` (up to 24 h by default). ``ORPHANED`` is
    # deliberately absent — the register-time reconciler can hoist it
    # back to RUNNING, so treating it as terminal would drop still-alive
    # work on the floor.
    _TERMINAL = {
        JobState.DONE,
        JobState.FAILED,
        JobState.CANCELLED,
        JobState.INTERRUPTED,
    }
    deadline = asyncio.get_event_loop().time() + timeout_s
    while True:
        job = await store.get(job_id)
        if job is not None and job.state in _TERMINAL:
            return job
        if asyncio.get_event_loop().time() > deadline:
            raise WorkflowRunError(f"job {job_id} did not terminate within {timeout_s:.0f}s")
        await asyncio.sleep(poll_s)


async def _collect_output_handles(handles: HandleBook, job_id: str) -> dict[str, str]:
    """Return ``{output_port_name -> handle_id}`` for a completed job.

    The node stamps every registered handle with its declared output port
    name (``output_port_name`` in ``HandleRegister``). We just invert the
    list back into a port-name lookup.
    """

    registered = await handles.list_by_job(job_id)
    by_port: dict[str, str] = {}
    for h in registered:
        if h.output_port_name:
            by_port[h.output_port_name] = h.handle_id
    return by_port


# ---------------------------------------------------------------------------
# Internal helper (mirrors gateway.app._push_job_update to avoid the cycle).
# ---------------------------------------------------------------------------


def _push_update(hub: FrontendHub, job: Job) -> None:
    from hololab.gateway.app import _push_job_update

    _push_job_update(hub, job)


# ---------------------------------------------------------------------------
# Continue/Fork single-node dispatch (V8 model)
# ---------------------------------------------------------------------------
#
# The lineage-first snapshot model expresses two dispatch operations:
#
#   Continue(S, N) : N ∉ dom(S) → append a fresh job to S at N.
#                    Same snapshot_id. Inputs must all already be
#                    resolvable in S (i.e., every incoming edge points
#                    at a graph node already attributed to S).
#   Fork(S, N)     : N ∈ dom(S) → create a child snapshot S-prime,
#                    parent_snapshot_id = S. Inherit every attribution
#                    from S except the one at N and every attribution
#                    at a downstream-of-N graph node (per the current
#                    workflow graph — the user's editing state is the
#                    authoritative topology, matching Q3's "data-form
#                    over graph-form" rule).
#
# ``dispatch_graph_node`` implements both; the caller (the REST endpoint)
# just passes a base snapshot and a graph node id and discovers which
# operation was performed via the returned dict.


class DispatchError(RuntimeError):
    """Raised when a Continue/Fork dispatch can't be honoured.

    The endpoint layer converts this into a 400 with the message —
    unlike ``WorkflowRunError`` which is a run-loop assertion.
    """


async def dispatch_graph_node(
    app: FastAPI,
    *,
    workflow_id: str,
    graph: WorkflowGraph,
    graph_node_id: str,
    base_snapshot_id: str | None,
) -> dict[str, Any]:
    """Execute one graph node against a base snapshot (Continue or Fork).

    Args:
        app: the FastAPI app (for state access).
        workflow_id: the workflow to run under.
        graph: the current draft graph — used for edge topology and to
            look up the target node's algorithm/params/assignment.
        graph_node_id: id of the node to run.
        base_snapshot_id: base snapshot the dispatch extends or forks.
            When None, a fresh snapshot is created (Continue on an
            empty snapshot).

    Returns:
        ``{snapshot_id, job_id, operation, parent_snapshot_id?, forked_at?}``.

    Raises:
        DispatchError: if the target node's upstream inputs aren't
            resolvable in the base snapshot, or the assigned compute
            node is offline.
    """

    from hololab.gateway.workflows import WorkflowStore, downstream_closure

    snapshot_jobs: SnapshotJobsStore = app.state.snapshot_jobs
    workflows_store: WorkflowStore = app.state.workflows
    jobs_store: JobsStore = app.state.jobs_store
    hub: FrontendHub = app.state.hub
    registry: NodeRegistry = app.state.registry

    node_by_id = {n.id: n for n in graph.nodes}
    if graph_node_id not in node_by_id:
        raise DispatchError(f"graph node {graph_node_id!r} not in current graph")
    gnode = node_by_id[graph_node_id]

    if gnode.assigned_node_id is None:
        raise DispatchError(f"graph node {graph_node_id!r} has no assigned compute node")

    # -- Choose Continue vs Fork --------------------------------------------
    # Attribute-at-creation (2026-09-22 refactor) means ``get_job_at``
    # can return a FAILED / CANCELLED job for a slot the operator will
    # want to Continue-retry, not Fork. Skip terminal-fail states here
    # so the retry stays a Continue: a new attempt appends a new
    # attribution row with newer ``created_ts``, and ``get_job_at`` (which
    # orders by created_ts DESC) surfaces the newest one at the next
    # dispatch. Slot semantics: "productively filled" = DONE or in
    # flight; "empty for Continue" = never attempted OR last attempt
    # failed/cancelled.
    existing_job_id: str | None = None
    parent_snapshot_id: str | None = None
    if base_snapshot_id is not None:
        candidate = await snapshot_jobs.get_job_at(base_snapshot_id, graph_node_id)
        if candidate is not None:
            candidate_job = await jobs_store.get(candidate)
            if candidate_job is not None and candidate_job.state not in (
                JobState.FAILED,
                JobState.CANCELLED,
            ):
                existing_job_id = candidate

    if base_snapshot_id is None:
        # No base → new snapshot, first attribution.
        snap = await workflows_store.create_snapshot(workflow_id=workflow_id, graph=graph)
        target_snapshot_id = snap.snapshot_id
        operation = "continue"
    elif existing_job_id is None:
        # Continue on the existing snapshot.
        target_snapshot_id = base_snapshot_id
        operation = "continue"
    else:
        # Fork: create a child snapshot; inherit every parent-snapshot
        # attribution except the target node and its downstream closure.
        forbidden = downstream_closure(graph, graph_node_id) | {graph_node_id}
        parent_attributions = await snapshot_jobs.list_attributions(base_snapshot_id)
        inherited = [
            (row["job_id"], row["graph_node_id"])
            for row in parent_attributions
            if row["graph_node_id"] not in forbidden
        ]
        parent_snapshot_id = base_snapshot_id
        snap = await workflows_store.create_snapshot(
            workflow_id=workflow_id,
            graph=graph,
            parent_snapshot_id=parent_snapshot_id,
        )
        target_snapshot_id = snap.snapshot_id
        await snapshot_jobs.attribute_many(target_snapshot_id, inherited)
        operation = "fork"

    # -- Resolve inputs from the target snapshot's existing attributions ----
    input_handles = await _resolve_inputs_from_snapshot(
        app,
        workflow_id=workflow_id,
        target_snapshot_id=target_snapshot_id,
        graph=graph,
        graph_node_id=graph_node_id,
    )

    # -- Assigned node must be online ----------------------------------------
    session = registry.get_session(gnode.assigned_node_id)
    if session is None:
        raise DispatchError(f"assigned compute node {gnode.assigned_node_id!r} is not online")

    # -- Fan-out routing: mirror the check ``run_snapshot`` does at
    # execution.py:111. Without this, a single-node dispatch of an
    # arrayable node with the arrayed toggle on would run the exec
    # ONCE against the array-root handles, and the pack's per-shard
    # input assumptions (e.g. ``{{ inputs.cams }}/cameras.txt``) would
    # blow up. See the header button in AlgorithmNode's card.
    pack_entry: dict[str, Any] | None = next(
        (
            c
            for c in registry.catalog_json()
            if c["name"] == gnode.algorithm_name and c["version"] == gnode.algorithm_version
        ),
        None,
    )
    needs_fanout = bool(
        gnode.arrayed_toggle and pack_entry is not None and pack_entry.get("arrayable", False)
    )

    if needs_fanout:
        # Prepare synchronously so we can return the parent job_id in
        # the API response; run the shard loop as a background task so
        # the endpoint stays fire-and-forget (matching the non-fanout
        # branch below). Attribution is now written at parent-job
        # CREATION inside ``_prepare_fanout`` (2026-09-22 refactor); the
        # background task only executes shards and marks terminal state.
        try:
            plan = await _prepare_fanout(
                app,
                snapshot_id=target_snapshot_id,
                workflow_id=workflow_id,
                gnode=gnode,
                pack_entry=pack_entry,
                input_handles=input_handles,
            )
        except WorkflowRunError as exc:
            raise DispatchError(str(exc)) from exc
        job_id = plan.parent_job.job_id

        async def _run_shards_in_background() -> None:
            try:
                await _execute_fanout_body(
                    app,
                    snapshot_id=target_snapshot_id,
                    workflow_id=workflow_id,
                    gnode=gnode,
                    plan=plan,
                    job_timeout_s=24 * 3600,
                )
            except WorkflowRunError:
                # Parent already marked FAILED inside the body. Attribution
                # row was written at creation (kept — see the block
                # comment on ``_prepare_fanout``'s attribute call); the
                # resolver's state check turns that into "last attempt
                # failed; re-run it" instead of the wrong "no produced
                # artifact" message. Under Continue-vs-Fork detection,
                # FAILED slots are treated as slot-empty so the retry
                # stays a Continue.
                return
            except Exception:
                log.exception(
                    "dispatch fan-out task crashed unexpectedly",
                    parent_job_id=plan.parent_job.job_id,
                    graph_node=gnode.id,
                )
                return
            # Success path: no follow-up attribute needed — the row
            # already exists (idempotent INSERT OR IGNORE). Kept as a
            # doc-comment marker.

        # Retain a reference so the GC doesn't reap the background task
        # mid-run (RUF006). Same pattern as ``app.py`` uses for full
        # ``workflow_run_tasks``.
        fanout_task = asyncio.create_task(
            _run_shards_in_background(),
            name=f"dispatch-fanout-{plan.parent_job.job_id}",
        )
        _bg_tasks: set[asyncio.Task[None]] = getattr(app.state, "dispatch_fanout_tasks", set())
        _bg_tasks.add(fanout_task)
        fanout_task.add_done_callback(_bg_tasks.discard)
        app.state.dispatch_fanout_tasks = _bg_tasks
    else:
        # Non-fanout: fire-and-forget dispatch. Attribution is written
        # at job creation inside ``_dispatch_job`` (see the block
        # comment on its ``snapshot_jobs`` parameter). The WS
        # ``job_done`` handler's own attribute call remains as
        # defense-in-depth (idempotent INSERT OR IGNORE).
        job_id = await _dispatch_job(
            registry=registry,
            store=jobs_store,
            hub=hub,
            snapshot_id=target_snapshot_id,
            workflow_id=workflow_id,
            gnode=gnode,
            input_handles=input_handles,
            pack_entry=pack_entry,
            snapshot_jobs=snapshot_jobs,
        )

    result: dict[str, Any] = {
        "snapshot_id": target_snapshot_id,
        "job_id": job_id,
        "operation": operation,
    }
    if parent_snapshot_id is not None:
        result["parent_snapshot_id"] = parent_snapshot_id
        result["forked_at"] = graph_node_id
    return result


async def _resolve_inputs_from_snapshot(
    app: FastAPI,
    *,
    workflow_id: str,
    target_snapshot_id: str,
    graph: WorkflowGraph,
    graph_node_id: str,
) -> dict[str, str]:
    """Read every incoming edge's upstream artifact from the snapshot.

    Under the lineage-first model, all upstream artifacts a job consumes
    must already be attributed to the same snapshot the new job is
    joining. That's the closure invariant. This helper resolves each
    input port → handle_id by:

      1. Following the edge to its source graph node.
      2. Looking up the job attributed to that source in the target
         snapshot (via ``snapshot_jobs.get_job_at``).
      3. Fetching that job's state (via ``jobs_store.get``) and
         branching:

         * ``DONE`` → look up its registered handles and wire the port.
         * ``PENDING / ASSIGNED / RUNNING`` → the upstream is still
           producing the artifact. Raise DispatchError with the shard
           progress so the operator waits instead of re-dispatching.
         * ``FAILED / CANCELLED`` → the last attempt didn't produce
           anything. Raise DispatchError telling the operator to
           re-run (a fresh dispatch will Continue on the same
           snapshot per ``dispatch_graph_node``'s "skip terminal-fail
           on Continue-vs-Fork" rule).

    A ``None`` from ``get_job_at`` means the slot has never been
    attempted in this snapshot — raise the original "run it first"
    message so the operator knows to trigger the upstream.

    Semantics upgrade (2026-09-22 refactor): attribution is now written
    at job CREATION (see ``_prepare_fanout`` and ``_dispatch_job``), so
    ``get_job_at`` returns a job as soon as it's dispatched, not only
    after DONE. The prior helper ``find_inflight_for_graph_node`` was a
    transitional workaround for the old "attribute-on-DONE" model and
    is no longer used.
    """

    snapshot_jobs: SnapshotJobsStore = app.state.snapshot_jobs
    jobs_store: JobsStore = app.state.jobs_store
    handles: HandleBook = app.state.handles

    wired: dict[str, str] = {}
    for edge in graph.edges:
        if edge.target != graph_node_id:
            continue
        source_gnode = edge.source
        upstream_job_id = await snapshot_jobs.get_job_at(target_snapshot_id, source_gnode)
        if upstream_job_id is None:
            # No attribution row at all — the upstream slot has never
            # been dispatched in this snapshot. Under the pre-refactor
            # "attribute-on-DONE" model this branch also caught the
            # mid-fan-out window; that window no longer exists because
            # ``_prepare_fanout`` attributes at creation.
            raise DispatchError(
                f"upstream graph node {source_gnode!r} has no produced artifact "
                f"in this snapshot; run it first"
            )
        upstream_job = await jobs_store.get(upstream_job_id)
        if upstream_job is None:
            # Row-level race: attribution exists but the job row is
            # missing. Treat as "no artifact" so the operator can
            # re-dispatch to recover.
            raise DispatchError(
                f"upstream graph node {source_gnode!r} attribution points at "
                f"unknown job {upstream_job_id!r}; re-run to recover"
            )
        state = upstream_job.state
        if state in (JobState.PENDING, JobState.ASSIGNED, JobState.RUNNING):
            # Attributed but still in flight. Aggregate shard progress
            # from the parent row when available; a non-fanout job has
            # None/None and the message drops the shard suffix.
            shards_done = upstream_job.progress_current
            shards_total = upstream_job.progress_total or upstream_job.expected_shards
            if shards_done is not None and shards_total is not None:
                progress = f" ({shards_done}/{shards_total} shards done)"
            elif shards_total is not None:
                progress = f" (0/{shards_total} shards done)"
            else:
                progress = ""
            raise DispatchError(
                f"upstream graph node {source_gnode!r} is still {state.value}"
                f"{progress}; wait for it to finish, then retry"
            )
        if state in (JobState.FAILED, JobState.CANCELLED):
            raise DispatchError(
                f"upstream graph node {source_gnode!r} last attempt {state.value}; re-run it"
            )
        # state is DONE → wire the handles.
        registered = await handles.list_by_job(upstream_job_id)
        by_port = {h.output_port_name: h.handle_id for h in registered if h.output_port_name}
        handle_id = by_port.get(edge.sourceHandle)
        if handle_id is None:
            raise DispatchError(
                f"upstream graph node {source_gnode!r} produced no output for port "
                f"{edge.sourceHandle!r}"
            )
        wired[edge.targetHandle] = handle_id
    return wired
