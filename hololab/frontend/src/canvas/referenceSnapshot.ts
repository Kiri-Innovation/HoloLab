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

function stateFor(resolved: ResolvedReference): Record<string, unknown> {
  const r = resolved.resource;
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
  const identity = { kind: resolved.kind, ref: resolved.ref };
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
