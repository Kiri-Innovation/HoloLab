// One renderer for every inserted HoloLab reference.  Its input is the
// gateway resolver DTO, so UI locations never invent competing snapshots.

export interface ResolvedReference {
  kind: string;
  ref: string;
  resource: Record<string, unknown>;
  related?: Record<string, string>;
}

function compact(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

// Keep whole-workflow references useful without embedding every parameter/handle.
const WORKFLOW_NODE_LIMIT = 50;

function workflowGraph(resource: Record<string, unknown>) {
  return resource.graph as {
    nodes: Array<{
      id: string;
      algorithm_name: string;
      algorithm_version: string;
      latest_run?: { state: string } | null;
    }>;
    edges: unknown[];
  } | undefined;
}

// Runs are lineage snapshots, not a single process lifetime. Reused jobs may
// predate the snapshot; do not invent run start/end times from job timestamps.
function runJobs(r: Record<string, unknown>): Record<string, unknown>[] {
  return Array.isArray(r.jobs) ? r.jobs : [];
}
function runState(r: Record<string, unknown>) {
  const jobs = runJobs(r);
  const counts: Record<string, number> = {};
  for (const j of jobs) {
    const state = String(j.state ?? "unknown");
    counts[state] = (counts[state] ?? 0) + 1;
  }
  const status = !jobs.length ? "pending" : counts.failed ? "failed"
    : counts.cancelled && !counts.running ? "cancelled"
    : jobs.every(j => j.state === "done") ? "done"
    : counts.running || counts.assigned ? "running" : "pending";
  return {
    resolver_status: r.resolver_status ?? "available",
    status: r.resolver_status === "unavailable" ? "unknown" : status,
    created_ts: r.created_ts ?? null,
    started_ts: null,
    ended_ts: null,
    trigger: null,
    timing_note: "Run start/end and trigger are not recorded; reused jobs may predate this snapshot.",
    job_count: r.resolver_status === "unavailable" ? null : jobs.length,
    state_counts: counts,
    state_basis: "associated_jobs_same_rollup_as_runs_list",
  };
}

function stateFor(resolved: ResolvedReference): Record<string, unknown> {
  const r = resolved.resource;
  if (resolved.kind === "run") return runState(r);
  if (resolved.kind === "workflow") {
    const graph = workflowGraph(r);
    return {
      resolver_status: r.resolver_status ?? "available",
      node_count: graph?.nodes.length ?? null,
      edge_count: graph?.edges.length ?? null,
      last_run: r.last_run ?? null,
    };
  }
  if (resolved.kind === "graph-node") {
    const latest = r.latest_attribution as Record<string, unknown> | null | undefined;
    return { latest_attribution_state: latest?.state ?? "not_run", drift: r.drift ?? false };
  }
  return {
    status: r.state ?? r.last_run_state ?? "unknown",
    fail_reason: r.fail_reason ?? null,
    progress: r.progress ?? null,
  };
}

function contextFor(resolved: ResolvedReference): Record<string, unknown> {
  const r = resolved.resource;
  if (resolved.kind === "run") {
    const nodes = workflowGraph(r)?.nodes ?? [];
    const jobs = runJobs(r);
    return {
      workflow_id: r.workflow_id ?? null,
      node_count: nodes.length,
      nodes: nodes.slice(0, 30).map(n => ({
        id: n.id, algorithm_name: n.algorithm_name, algorithm_version: n.algorithm_version,
      })),
      jobs: jobs.slice(0, 30).map(j => ({
        job_id: j.job_id, graph_node_id: j.graph_node_id,
        algorithm_name: j.algorithm_name, state: j.state,
      })),
      omitted_node_count: Math.max(0, nodes.length - 30),
      omitted_job_count: Math.max(0, jobs.length - 30),
    };
  }
  if (resolved.kind === "workflow") {
    const nodes = workflowGraph(r)?.nodes ?? [];
    const stateCounts: Record<string, number> = {};
    for (const node of nodes) {
      const state = node.latest_run?.state ?? "not_run";
      stateCounts[state] = (stateCounts[state] ?? 0) + 1;
    }
    return {
      source: "saved_workflow_draft",
      node_state_basis: "latest_run_per_node_across_workflow_snapshots",
      node_state_counts: stateCounts,
      nodes: nodes.slice(0, WORKFLOW_NODE_LIMIT).map((node) => ({
        id: node.id,
        algorithm: `${node.algorithm_name}@${node.algorithm_version}`,
        state: node.latest_run?.state ?? "not_run",
      })),
      omitted_node_count: Math.max(0, nodes.length - WORKFLOW_NODE_LIMIT),
    };
  }
  if (resolved.kind === "graph-node") {
    return {
      current_draft: r.current_draft ?? {
        algorithm_name: r.algorithm_name,
        algorithm_version: r.algorithm_version,
        params: r.params,
        assigned_node_id: r.assigned_node_id,
      },
      latest_attribution: r.latest_attribution ?? null,
      upstream: r.upstream ?? [],
      downstream: r.downstream ?? [],
    };
  }
  if (resolved.kind === "job") {
    return {
      workflow_id: r.workflow_id,
      snapshot_id: r.snapshot_id,
      graph_node_id: r.graph_node_id,
      algorithm: `${r.algorithm_name ?? "unknown"}@${r.algorithm_version ?? "unknown"}`,
      params: r.params ?? {},
      input_handles: r.input_handles ?? {},
      output_handles: r.output_handles ?? {},
      timestamps: { created_ts: r.created_ts, started_ts: r.started_ts, updated_ts: r.updated_ts },
    };
  }
  return r;
}

export function referenceSnapshotDocument(
  resolved: ResolvedReference,
  instanceBaseUrl = window.location.origin,
): string {
  const resolveUrl = new URL("/api/resolve", instanceBaseUrl);
  resolveUrl.searchParams.set("ref", resolved.ref);
  const navigation = {
    instance_base_url: instanceBaseUrl,
    resolve: resolveUrl.toString(),
    ...(resolved.kind === "run" && resolved.resource.snapshot_id ? {
      run_record: `/api/snapshots/${resolved.resource.snapshot_id}`,
    } : {}),
    ...(resolved.related ?? {}),
  };
  const identity = {
    kind: resolved.kind === "run" ? "external:hololab:run" : resolved.kind,
    ref: resolved.ref,
    ...(resolved.kind === "run" ? { run_id: resolved.resource.snapshot_id ?? null } : {}),
    ...(resolved.kind === "workflow" ? {
      workflow_id: resolved.resource.workflow_id ?? null,
      name: resolved.resource.name ?? null,
    } : {}),
  };
  return [
    "# HoloLab reference snapshot",
    "",
    "- schema: `hololab.reference-snapshot/v1`",
    `- kind: \`${identity.kind}\``,
    `- ref: \`${resolved.ref}\``,
    "",
    "## identity",
    "```json",
    compact(identity),
    "```",
    "",
    "## state",
    "```json",
    compact(stateFor(resolved)),
    "```",
    "",
    "## context",
    "```json",
    compact(contextFor(resolved)),
    "```",
    "",
    "## navigation",
    "```json",
    compact(navigation),
    "```",
  ].join("\n");
}

export async function resolveReference(ref: string): Promise<ResolvedReference> {
  const query = new URLSearchParams({ ref });
  const response = await fetch(`/api/resolve?${query.toString()}`);
  if (!response.ok) throw new Error(`reference resolver returned ${response.status}`);
  return response.json() as Promise<ResolvedReference>;
}
