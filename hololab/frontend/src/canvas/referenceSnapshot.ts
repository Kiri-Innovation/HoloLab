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

function stateFor(resolved: ResolvedReference): Record<string, unknown> {
  const r = resolved.resource;
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
    ...(resolved.related ?? {}),
  };
  const identity = {
    kind: resolved.kind,
    ref: resolved.ref,
    ...(resolved.kind === "workflow" ? {
      workflow_id: resolved.resource.workflow_id ?? null,
      name: resolved.resource.name ?? null,
    } : {}),
  };
  return [
    "# HoloLab reference snapshot",
    "",
    "- schema: `hololab.reference-snapshot/v1`",
    `- kind: \`${resolved.kind}\``,
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
