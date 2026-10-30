"""Draft evidence selection; never changes frozen run membership."""

from hololab.constants import is_constant
from hololab.gateway.preparation import changed_nodes, digest, historical_execution
from hololab.gateway.workflows import downstream_closure, topological_order


def select_canvas_jobs(graph, candidates, sources):
    """Select one compatible generation per slot, including in-flight attempts.

    candidates are newest-first (snapshot, then parent job creation). Absence in
    a newer snapshot is not invalidation. Configuration and producer identity
    are: a downstream result cannot be combined with a different upstream job.
    Never fall back to an older success after a newer attempt is invalid/failed.
    """
    chosen = {}
    for sid, job in candidates:
        chosen.setdefault(job["graph_node_id"], (sid, job))
    accepted = {}
    invalid_by_source = {}
    for sid, (frozen, _) in sources.items():
        dirty = changed_nodes(graph, frozen)
        for nid in list(dirty):
            dirty |= downstream_closure(graph, nid) | downstream_closure(frozen, nid)
        invalid_by_source[sid] = dirty
    nodes = {n.id: n for n in graph.nodes}
    for nid in topological_order(graph):
        if nid not in chosen:
            continue
        sid, job = chosen[nid]
        if nid in invalid_by_source[sid]:
            continue
        if "params" in job and digest(job["params"]) != digest(nodes[nid].params):
            continue
        _, source_jobs = sources[sid]
        source_parents = {
            j["graph_node_id"]: j
            for j in sorted(source_jobs, key=lambda j: (j.get("created_ts", 0), j["job_id"]))
            if not j.get("parent_job_id")
        }
        valid = True
        for edge in graph.edges:
            if edge.target != nid or is_constant(nodes[edge.source]):
                continue
            upstream = accepted.get(edge.source)
            recorded = source_parents.get(edge.source)
            if not upstream or not recorded or upstream[1]["job_id"] != recorded["job_id"]:
                valid = False
                break
        if valid:
            accepted[nid] = (sid, job)
    result = []
    for sid, parent in accepted.values():
        # Only this parent's shards; older generations cannot contaminate status.
        for job in sources[sid][1]:
            if job["job_id"] == parent["job_id"] or job.get("parent_job_id") == parent["job_id"]:
                result.append({**job, "snapshot_id": sid})
    return result


async def canvas_jobs(app, workflow_id, graph):
    # Rank all history without a UI-sized window. Only hydrate snapshots that
    # supply a winning parent, not every snapshot's handles.
    async with (
        app.state.db.read() as conn,
        conn.execute(
            """
        WITH ranked AS (
          SELECT sj.snapshot_id, sj.graph_node_id, sj.job_id,
                 ROW_NUMBER() OVER (PARTITION BY sj.graph_node_id
                   ORDER BY s.created_ts DESC, j.created_ts DESC, j.job_id DESC) rn
          FROM snapshot_jobs sj JOIN snapshots s ON s.snapshot_id=sj.snapshot_id
          JOIN jobs j ON j.job_id=sj.job_id
          WHERE s.workflow_id=? AND j.parent_job_id IS NULL
        ) SELECT snapshot_id, graph_node_id, job_id FROM ranked WHERE rn=1
    """,
            (workflow_id,),
        ) as cur,
    ):
        rows = await cur.fetchall()
    sources = {}
    for sid, _, _ in rows:
        if sid not in sources:
            snapshot = await app.state.workflows.get_snapshot(sid)
            sources[sid] = (
                await historical_execution(app, snapshot),
                await app.state.jobs_store.list_by_snapshot(sid),
            )
    candidates = []
    for sid, _nid, jid in rows:
        job = next((j for j in sources[sid][1] if j["job_id"] == jid), None)
        if job:
            candidates.append((sid, job))
    return select_canvas_jobs(graph, candidates, sources)


async def materialize_current_results(app, workflow_id, graph, snapshot_id, *, cleared):
    """Make one new snapshot's membership match the canvas result set.

    ``canvas_jobs`` is the pre-existing authority for a node's *current
    effective result*.  A run clears its requested topology region; every
    other completed effective result, including one selected from a sibling
    branch, is attributed to the new snapshot by original job id.
    """
    cleared = set(cleared)
    selected = await canvas_jobs(app, workflow_id, graph)
    parents = [
        job for job in selected
        if not job.get("parent_job_id")
        and job.get("graph_node_id") not in cleared
    ]
    # Historical rerun rows are pointers, not artifact producers.  Preserve
    # the many-to-many membership on the origin job just as rerun-from did.
    materialized = []
    for job in parents:
        origin_id = job["job_id"]
        origin = await app.state.jobs_store.get(origin_id)
        while origin is not None and origin.reused_from_job_id:
            origin_id = origin.reused_from_job_id
            origin = await app.state.jobs_store.get(origin_id)
        materialized.append({**job, "job_id": origin_id})
    await app.state.snapshot_jobs.attribute_many(
        snapshot_id,
        [(job["job_id"], job["graph_node_id"]) for job in materialized],
    )
    outputs = {}
    for job in materialized:
        if job.get("state") != "done":
            continue
        produced = await app.state.handles.list_by_job(job["job_id"])
        outputs[job["graph_node_id"]] = {
            handle.output_port_name: handle.handle_id
            for handle in produced
            if handle.output_port_name
        }
    return outputs
