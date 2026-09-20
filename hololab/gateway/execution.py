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
    topological_order,
)
from hololab.logging import get_logger
from hololab.protocol import JobAssign, JobFailReason, encode

log = get_logger("gateway.exec")


class WorkflowRunError(RuntimeError):
    """Raised for run-time problems (upstream failed, node dropped, timeout)."""


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
            await snapshot_jobs.attribute(snapshot_id, parent_job_id, graph_node_id)
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
        )
        job_ids.append(job_id)

        # Wait for terminal state.
        final = await _await_job_terminal(store, job_id, timeout_s=job_timeout_s)
        if final.state is not JobState.DONE:
            raise WorkflowRunError(
                f"job {job_id} for graph node {graph_node_id!r} finished in state "
                f"{final.state.value}"
            )

        # Lineage-first bridge: attribute this done job to the current
        # snapshot. Historically this was implicit in ``jobs.snapshot_id``;
        # under the V8 model every done attribution has to be an explicit
        # ``snapshot_jobs`` row so the Continue/Fork dispatcher can find
        # it by (snapshot_id, graph_node_id).
        await snapshot_jobs.attribute(snapshot_id, job_id, graph_node_id)

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

    parent_job = Job(
        job_id=str(uuid.uuid4()),
        workflow_id=workflow_id,
        snapshot_id=snapshot_id,
        algorithm_name=gnode.algorithm_name,
        algorithm_version=gnode.algorithm_version,
        params=dict(gnode.params),
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
    shards: list[tuple[int, str, Job]] = []  # (idx, element_id, shard_job)
    for idx, element_id in enumerate(plan.element_ids):
        shard_inputs = await _shard_input_handles(
            handles=handles,
            input_handles=plan.input_handles,
            arrayed_input_ports=plan.arrayed_input_ports,
            element_id=element_id,
            producer_node_id=plan.session_node_id,
        )
        shard = await _create_shard_row(
            store=store,
            hub=hub,
            snapshot_id=snapshot_id,
            workflow_id=workflow_id,
            gnode=gnode,
            parent_job_id=plan.parent_job.job_id,
            shard_element_id=element_id,
            shard_input_handles=shard_inputs,
        )
        shards.append((idx, element_id, shard))

    # -- Phase 2: bounded-concurrent dispatch --------------------------------
    parallelism = max(1, int(getattr(gnode, "parallelism", 1) or 1))
    sem = asyncio.Semaphore(parallelism)

    async def _run_one(idx: int, element_id: str, shard: Job) -> tuple[int, str, Job]:
        async with sem:
            await _dispatch_prepared_shard(
                registry=registry,
                store=store,
                hub=hub,
                gnode=gnode,
                shard=shard,
                shard_element_id=element_id,
                shard_output_prefix=plan.parent_ws,
            )
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
        *(_run_one(i, eid, s) for (i, eid, s) in shards),
        return_exceptions=True,
    )

    # Collect every failure before marking the parent — we do not fail-fast
    # (v1 semantics per product spec). Truncate the reason to keep the DB
    # column bounded when a very wide fan-out fails wholesale.
    failures: list[str] = []
    for r in results:
        if isinstance(r, BaseException):
            failures.append(f"shard raised: {r!r}")
            continue
        idx, element_id, final = r
        if final.state is not JobState.DONE:
            msg = f"shard {idx} (element {element_id!r}) finished {final.state.value}"
            if final.fail_message:
                msg += f": {final.fail_message}"
            failures.append(msg)
    if failures:
        reason = "; ".join(failures[:5])
        if len(failures) > 5:
            reason += f"; +{len(failures) - 5} more"
        await _mark_parent_failed(store, hub, parent_running, reason=reason)
        raise WorkflowRunError(f"fanout for graph node {gnode.id!r}: {reason}")

    parent_outputs: dict[str, str] = {}
    for port_name, spec in plan.pack_outputs.items():
        aggregate_path = str(Path(plan.parent_ws) / port_name)
        try:
            size_bytes = _dir_size_bytes(Path(aggregate_path))
        except OSError:
            size_bytes = None
        aggregate = Handle(
            handle_id=str(uuid.uuid4()),
            node_id=plan.session_node_id,
            storage=spec.get("storage", "dir"),
            tags=list(spec.get("tags", [])),
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
                e.name
                for e in it
                if not e.name.startswith(".") and e.is_dir(follow_symlinks=True)
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
) -> Job:
    """Create one shard job row in ``PENDING``. No transition, no send.

    Split out from :func:`_dispatch_prepared_shard` so a bounded-concurrent
    fan-out can materialise every shard row upfront (giving the snapshot
    detail endpoint / RecentJobsPanel the full row set from t=0) and then
    stagger the ASSIGNED transitions through a semaphore.
    """

    shard = Job(
        job_id=str(uuid.uuid4()),
        workflow_id=workflow_id,
        snapshot_id=snapshot_id,
        algorithm_name=gnode.algorithm_name,
        algorithm_version=gnode.algorithm_version,
        params=dict(gnode.params),
        input_handles=shard_input_handles,
        graph_node_id=gnode.id,
        parent_job_id=parent_job_id,
        shard_element_id=shard_element_id,
    )
    await store.create(shard)
    _push_update(hub, shard)
    return shard


async def _dispatch_prepared_shard(
    *,
    registry: NodeRegistry,
    store: JobsStore,
    hub: FrontendHub,
    gnode: GraphNode,
    shard: Job,
    shard_element_id: str,
    shard_output_prefix: str,
) -> None:
    """Transition an already-created shard PENDING → ASSIGNED and send its
    :class:`JobAssign` frame to the compute node. The compute-node session
    is re-looked-up here (not passed in) so a session drop mid-fanout
    surfaces as a per-shard failure rather than a wholesale crash.
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
    )
    frame = encode("job_assign", assign_msg, v=session.protocol_v)
    async with session.send_lock:
        await session.ws.send_text(frame)

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
) -> None:
    """Transition a parent job to FAILED when a shard fails."""

    parent_failed = JobStateMachine.transition(
        parent_running,
        JobState.FAILED,
        fail_reason=JobFailReason.USER_ERROR,
        fail_message=reason,
    )
    _, payload = event_from_transition(parent_running, parent_failed)
    await store.update(parent_failed, "transition:failed", payload)
    _push_update(hub, parent_failed)


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
    """

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
) -> str:
    """Create + assign + send job_assign. Returns the newly minted job id."""

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
        params=dict(gnode.params),
        input_handles=input_handles,
        graph_node_id=gnode.id,
    )
    await store.create(job)
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
    async with session.send_lock:
        await session.ws.send_text(frame)

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

    _TERMINAL = {JobState.DONE, JobState.FAILED, JobState.CANCELLED}
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
    existing_job_id: str | None = None
    parent_snapshot_id: str | None = None
    if base_snapshot_id is not None:
        existing_job_id = await snapshot_jobs.get_job_at(base_snapshot_id, graph_node_id)

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
        # branch below). Attribution happens inside the task when the
        # parent completes, mirroring ``run_snapshot`` line 128.
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
                # Parent already marked FAILED inside the body; no
                # attribution written so the operator can re-dispatch.
                return
            except Exception:
                log.exception(
                    "dispatch fan-out task crashed unexpectedly",
                    parent_job_id=plan.parent_job.job_id,
                    graph_node=gnode.id,
                )
                return
            try:
                await snapshot_jobs.attribute(target_snapshot_id, plan.parent_job.job_id, gnode.id)
            except Exception:
                log.exception(
                    "attribution failed after fan-out done",
                    parent_job_id=plan.parent_job.job_id,
                    graph_node=gnode.id,
                )

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
        # Non-fanout: fire-and-forget dispatch; attribution happens in
        # the WS ``job_done`` handler when the subprocess reports DONE.
        job_id = await _dispatch_job(
            registry=registry,
            store=jobs_store,
            hub=hub,
            snapshot_id=target_snapshot_id,
            workflow_id=workflow_id,
            gnode=gnode,
            input_handles=input_handles,
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
         snapshot (via snapshot_jobs).
      3. Reading that job's registered output handles (via HandleBook)
         and picking the port named on the edge.

    A missing attribution or a missing output raises DispatchError so
    the endpoint layer can surface a human-readable "upstream X hasn't
    produced Y yet" message.
    """

    snapshot_jobs: SnapshotJobsStore = app.state.snapshot_jobs
    handles: HandleBook = app.state.handles

    wired: dict[str, str] = {}
    for edge in graph.edges:
        if edge.target != graph_node_id:
            continue
        source_gnode = edge.source
        upstream_job_id = await snapshot_jobs.get_job_at(target_snapshot_id, source_gnode)
        if upstream_job_id is None:
            raise DispatchError(
                f"upstream graph node {source_gnode!r} has no produced artifact "
                f"in this snapshot; run it first"
            )
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
