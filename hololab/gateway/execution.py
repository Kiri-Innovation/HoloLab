"""Sequential DAG executor for workflow snapshots.

Executes one snapshot start-to-finish: topologically sorts the algorithm
nodes, dispatches them one at a time to their assigned compute nodes, and
threads each upstream job's output handles into the downstream node's
``input_handles``. Stops on the first failure.

Concurrency: this MVP is deliberately sequential. A single ``WorkflowRunner``
runs one snapshot per call; multiple runs against the same gateway are fine
because each ``run_snapshot`` is a bounded coroutine and holds no global
state beyond the DB writes it makes.

Not covered here (see ``docs/workflow-schema.md#non-goals``): parallel
branches, retries, rerun-with-changes.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import FastAPI

from hololab.gateway.handles import HandleBook
from hololab.gateway.hub import FrontendHub
from hololab.gateway.jobs import Job, JobState, JobStateMachine, event_from_transition
from hololab.gateway.registry import JobsStore, NodeRegistry, SnapshotJobsStore
from hololab.gateway.workflows import (
    GraphNode,
    WorkflowGraph,
    topological_order,
)
from hololab.logging import get_logger
from hololab.protocol import JobAssign, encode

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
        raise DispatchError(
            f"graph node {graph_node_id!r} has no assigned compute node"
        )

    # -- Choose Continue vs Fork --------------------------------------------
    existing_job_id: str | None = None
    parent_snapshot_id: str | None = None
    if base_snapshot_id is not None:
        existing_job_id = await snapshot_jobs.get_job_at(base_snapshot_id, graph_node_id)

    if base_snapshot_id is None:
        # No base → new snapshot, first attribution.
        snap = await workflows_store.create_snapshot(
            workflow_id=workflow_id, graph=graph
        )
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
        raise DispatchError(
            f"assigned compute node {gnode.assigned_node_id!r} is not online"
        )

    # -- Dispatch the actual job --------------------------------------------
    job_id = await _dispatch_job(
        registry=registry,
        store=jobs_store,
        hub=hub,
        snapshot_id=target_snapshot_id,
        workflow_id=workflow_id,
        gnode=gnode,
        input_handles=input_handles,
    )

    # We don't wait for the job here — the REST call returns while the
    # subprocess runs. The snapshot_jobs attribution happens when the
    # job reaches DONE (in the job_done handler, not here). If the job
    # fails / is cancelled, no attribution is written and the slot in
    # target_snapshot_id stays empty — the user can re-dispatch.

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
