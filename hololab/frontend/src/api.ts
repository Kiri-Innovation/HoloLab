// Thin REST client. All calls are same-origin — in dev, Vite proxies /api and
// /proxy to the gateway on :8828 (see vite.config.ts).

import type {
  CatalogPack,
  ComputeNode,
  HandleInfo,
  HandleSummary,
  JobDetail,
  LogTail,
  NodeEffectiveConfig,
  RunSummaryRow,
  SnapshotDeleteResult,
  SnapshotDeletionPreview,
  SnapshotDetail,
  WorkflowGraph,
} from "./wire";

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try {
      detail = await res.json();
    } catch {
      /* ignore body-parse errors */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    // FastAPI wraps HTTPException detail in {"detail": "..."}. Surface it
    // as the error message so callers using err.message get human-readable
    // text instead of the bare "HTTP 400" / "HTTP 500" status string.
    let msg: string;
    if (typeof detail === "string") {
      msg = detail;
    } else if (
      detail !== null &&
      typeof detail === "object" &&
      "detail" in (detail as object) &&
      typeof (detail as Record<string, unknown>).detail === "string"
    ) {
      msg = (detail as Record<string, unknown>).detail as string;
    } else {
      msg = `HTTP ${status}`;
    }
    super(msg);
    this.status = status;
    this.detail = detail;
  }
}

// -- catalog / online state --------------------------------------------------

export const getPackCatalog = () =>
  fetch("/api/pack-catalog").then(json<CatalogPack[]>);

export const getComputeNodes = () =>
  fetch("/api/nodes").then(json<ComputeNode[]>);

export const getRecentJobs = () =>
  fetch("/api/jobs").then(json<Array<Record<string, unknown>>>);

// Job detail — used by the log viewer to enrich the RecentJobRow with the
// bigger ``fail_message`` blob (the panel row only carries ``fail_reason``).
export const getJob = (job_id: string) =>
  fetch(`/api/jobs/${encodeURIComponent(job_id)}`).then(json<JobDetail>);

// Tail persisted stdout / stderr for one job. Default tail is high (10k)
// because most jobs write far less than that and the endpoint pulls one
// SQLite range scan either way — cheaper than paging.
export const tailJobLog = (
  job_id: string,
  opts?: { tail?: number; stream?: "stdout" | "stderr" | "both" },
) => {
  const p = new URLSearchParams();
  if (opts?.tail !== undefined) p.set("tail", String(opts.tail));
  if (opts?.stream) p.set("stream", opts.stream);
  const qs = p.toString();
  return fetch(
    `/api/jobs/${encodeURIComponent(job_id)}/log${qs ? "?" + qs : ""}`,
  ).then(json<LogTail>);
};

export const getHandle = (handle_id: string) =>
  fetch(`/api/handles/${handle_id}`).then(json<HandleInfo>);

export const getHandleSummary = (handle_id: string) =>
  fetch(`/api/handles/${handle_id}/summary`).then(json<HandleSummary>);

// -- workflows ---------------------------------------------------------------

// Last-run summary attached to each Gallery row so the card can render
// its state pip + timestamp without a follow-up round trip.
export interface WorkflowLastRun {
  snapshot_id: string;
  created_ts: number;
  state: string;
  state_counts: Record<string, number>;
  job_count: number;
}

export interface WorkflowSummary {
  workflow_id: string;
  name: string;
  node_count: number;
  created_ts: number;
  updated_ts: number;
  last_run: WorkflowLastRun | null;
}

export const listWorkflows = () =>
  fetch("/api/workflows").then(json<WorkflowSummary[]>);

export const getWorkflow = (workflow_id: string) =>
  fetch(`/api/workflows/${workflow_id}`).then(
    json<{
      workflow_id: string;
      name: string;
      graph: WorkflowGraph;
      created_ts: number;
      updated_ts: number;
    }>,
  );

export const saveWorkflow = (body: {
  workflow_id?: string;
  name: string;
  graph: WorkflowGraph;
}) =>
  fetch("/api/workflows", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then(json<{ workflow_id: string; name: string; updated_ts: number }>);

export const deleteWorkflow = (workflow_id: string) =>
  fetch(`/api/workflows/${workflow_id}`, { method: "DELETE" }).then(
    json<{ workflow_id: string; state: string }>,
  );

export const runWorkflow = (workflow_id: string) =>
  fetch(`/api/workflows/${workflow_id}/run`, { method: "POST" }).then(
    json<{ workflow_id: string; snapshot_id: string; node_count: number }>,
  );

// -- run history --------------------------------------------------------------

export const listWorkflowRuns = (workflow_id: string) =>
  fetch(`/api/workflows/${workflow_id}/runs`).then(json<RunSummaryRow[]>);

export const patchRun = (
  workflow_id: string,
  snapshot_id: string,
  patch: { favorite?: boolean; note?: string | null },
) =>
  fetch(`/api/workflows/${workflow_id}/runs/${snapshot_id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  }).then(json<{ snapshot_id: string; favorite: boolean; note: string | null }>);

export const getSnapshot = (snapshot_id: string) =>
  fetch(`/api/snapshots/${snapshot_id}`).then(json<SnapshotDetail>);

// Read-only preview: how a would-be snapshot-delete would resolve
// under the ref-counting rule (kept vs removed artifacts, freed bytes,
// blocking live jobs). Powers the confirm modal on Run History's
// right-click "delete run + artifacts" menu.
export const previewSnapshotDeletion = (snapshot_id: string) =>
  fetch(`/api/snapshots/${snapshot_id}/deletion-preview`).then(
    json<SnapshotDeletionPreview>,
  );

// Fire the delete. Idempotent: unknown snapshot returns
// ``{state: "gone"}``. Refuses with 409 if any attributed job is still
// non-terminal (pending / assigned / running) — the caller should
// surface the ``live_jobs`` list from the error detail and suggest
// cancelling the run first.
export const deleteSnapshot = (snapshot_id: string) =>
  fetch(`/api/snapshots/${snapshot_id}`, { method: "DELETE" }).then(
    json<SnapshotDeleteResult>,
  );

export const restoreFromSnapshot = (workflow_id: string, snapshot_id: string) =>
  fetch(
    `/api/workflows/${workflow_id}/restore-from-snapshot/${snapshot_id}`,
    { method: "POST" },
  ).then(
    json<{
      workflow_id: string;
      name: string;
      restored_from_snapshot_id: string;
      updated_ts: number;
    }>,
  );

export const rerunFromNode = (snapshot_id: string, graph_node_id: string) =>
  fetch(
    `/api/snapshots/${snapshot_id}/rerun-from/${encodeURIComponent(graph_node_id)}`,
    { method: "POST" },
  ).then(
    json<{
      workflow_id: string;
      original_snapshot_id: string;
      new_snapshot_id: string;
      rerun_from_graph_node_id: string;
      rerun_graph_node_ids: string[];
      reused_graph_node_ids: string[];
      reused_job_ids: string[];
    }>,
  );

// Cosmetic patch — updates the draft's ``preview_open`` (and future
// cosmetic fields) on one graph node, mirroring to the workflow's
// latest snapshot so switching to the "last run" view preserves the
// drawer state. Structural fields (params, edges, assigned_node_id)
// cannot be patched through this endpoint — see the classification
// table in docs/workflow-schema.md.
export const patchWorkflowGraphNodeCosmetic = (
  workflow_id: string,
  graph_node_id: string,
  patch: { preview_open?: string | null; position?: { x: number; y: number } },
) =>
  fetch(
    `/api/workflows/${workflow_id}/graph-nodes/${encodeURIComponent(graph_node_id)}/cosmetic`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    },
  ).then(
    json<{
      workflow_id: string;
      graph_node_id: string;
      applied: Record<string, unknown>;
      mirrored_to: string | null;
    }>,
  );

// Same idea as the workflow-side patch but for a specific snapshot's
// graph_json. Structurally immutable, cosmetically mutable — see
// docs/workflow-schema.md.
export const patchSnapshotGraphNodeCosmetic = (
  snapshot_id: string,
  graph_node_id: string,
  patch: { preview_open?: string | null; position?: { x: number; y: number } },
) =>
  fetch(
    `/api/snapshots/${snapshot_id}/graph-nodes/${encodeURIComponent(graph_node_id)}/cosmetic`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    },
  ).then(
    json<{
      snapshot_id: string;
      graph_node_id: string;
      applied: Record<string, unknown>;
    }>,
  );

// V8 Continue/Fork model: dispatch one graph node against a base
// snapshot. The endpoint decides Continue (extend the base) vs Fork
// (create a child snapshot) based on whether the target node already
// has an artifact in the base. ``base_snapshot_id`` defaults to the
// workflow's most recent snapshot when omitted.
export const dispatchNode = (
  workflow_id: string,
  graph_node_id: string,
  opts?: { base_snapshot_id?: string },
) => {
  const qs = opts?.base_snapshot_id
    ? `?base_snapshot_id=${encodeURIComponent(opts.base_snapshot_id)}`
    : "";
  return fetch(
    `/api/workflows/${workflow_id}/dispatch/${encodeURIComponent(graph_node_id)}${qs}`,
    { method: "POST" },
  ).then(
    json<{
      snapshot_id: string;
      job_id: string;
      operation: "continue" | "fork";
      parent_snapshot_id?: string;
      forked_at?: string;
    }>,
  );
};

// Artifact provenance DAG.
export const getArtifactLineage = (
  handle_id: string,
  opts?: { direction?: "up" | "down" | "both"; max_depth?: number },
) => {
  const q = new URLSearchParams();
  if (opts?.direction) q.set("direction", opts.direction);
  if (opts?.max_depth) q.set("max_depth", String(opts.max_depth));
  const qs = q.toString();
  return fetch(
    `/api/artifacts/${encodeURIComponent(handle_id)}/lineage${qs ? "?" + qs : ""}`,
  ).then(
    json<{
      root: string;
      nodes: Array<{
        artifact_id: string;
        producing_job_id: string | null;
        algorithm: string | null;
        version: string | null;
        params: Record<string, unknown>;
        node_id: string;
        storage: string;
        output_port_name: string | null;
        size_bytes: number | null;
        created_ts: number;
        deleted_ts: number | null;
      }>;
      edges: Array<{
        parent: string;
        child: string;
        consuming_port_name: string | null;
        consuming_job_id: string;
      }>;
    }>,
  );
};

// -- node config -------------------------------------------------------------
//
// The Compute-nodes panel opens a drawer per node that reads and edits
// the fields the node exposes as "editable" (workspace_root, legacy
// roots, node_name, advertised_url).

export const getNodeConfig = (node_id: string) =>
  fetch(`/api/nodes/${node_id}/config`).then(
    json<{ node_id: string; config: NodeEffectiveConfig }>,
  );

export const patchNodeConfig = (
  node_id: string,
  patch: Partial<
    Pick<
      NodeEffectiveConfig,
      | "workspace_root"
      | "legacy_workspace_roots"
      | "pack_dirs"
      | "node_name"
      | "advertised_url"
      | "flops_executor_id"
    >
  >,
) =>
  fetch(`/api/nodes/${node_id}/config`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ patch }),
  }).then(json<{ node_id: string; config: NodeEffectiveConfig }>);

// -- artifacts ---------------------------------------------------------------
//
// The Artifacts page (``/artifacts``) hits these to render the inventory and
// drive per-row / bulk cleanup. The gateway keeps handles in its DB; the
// files themselves live on the producing node. State is either a DB-cheap
// bucket (``pending`` / ``deleted``) or, when the user opts into a live
// filesystem check, one of ``alive`` / ``incomplete`` / ``dead``.

export type ArtifactState =
  | "alive"
  | "incomplete"
  | "dead"
  | "deleted"
  | "pending";

export interface ArtifactRow {
  handle_id: string;
  node_id: string;
  node_name: string | null;
  workflow_id: string | null;
  workflow_name: string | null;
  snapshot_id: string | null;
  job_id: string | null;
  algorithm_name: string | null;
  algorithm_version: string | null;
  output_port_name: string | null;
  tags: string[];
  storage: string;
  path: string;
  size_bytes: number | null;
  created_ts: number;
  deleted_ts: number | null;
  state: ArtifactState;
  live_size_bytes: number | null;
  live_mtime: number | null;
}

export interface ArtifactListResponse {
  rows: ArtifactRow[];
  counts: Partial<Record<ArtifactState, number>>;
  total_bytes: number;
  total_rows: number;
  checked: boolean;
}

export interface ArtifactSummary {
  workflows: Array<{
    workflow_id: string;
    workflow_name: string | null;
    total_bytes: number;
    count: number;
    deleted_count: number;
  }>;
  total_bytes: number;
  total_count: number;
}

export const listArtifacts = (opts?: {
  workflow_id?: string;
  snapshot_id?: string;
  job_id?: string;
  node_id?: string;
  state?: ArtifactState;
  check?: boolean;
  limit?: number;
}) => {
  const p = new URLSearchParams();
  if (opts?.workflow_id) p.set("workflow_id", opts.workflow_id);
  if (opts?.snapshot_id) p.set("snapshot_id", opts.snapshot_id);
  if (opts?.job_id) p.set("job_id", opts.job_id);
  if (opts?.node_id) p.set("node_id", opts.node_id);
  if (opts?.state) p.set("state", opts.state);
  if (opts?.check) p.set("check", "1");
  if (opts?.limit !== undefined) p.set("limit", String(opts.limit));
  const qs = p.toString();
  return fetch(`/api/artifacts${qs ? "?" + qs : ""}`).then(
    json<ArtifactListResponse>,
  );
};

export const getArtifactsSummary = () =>
  fetch("/api/artifacts/summary").then(json<ArtifactSummary>);

export const deleteArtifact = (handle_id: string) =>
  fetch(`/api/artifacts/${handle_id}`, { method: "DELETE" }).then(
    json<{ handle_id: string; state: string; freed_bytes: number | null }>,
  );

export const bulkDeleteArtifacts = (body: {
  workflow_id?: string;
  snapshot_id?: string;
  only_dead?: boolean;
}) =>
  fetch("/api/artifacts/delete-bulk", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then(
    json<{
      requested: number;
      results: Array<{
        handle_id: string;
        state: string;
        freed_bytes?: number;
        error?: string;
      }>;
      freed_bytes_total: number;
    }>,
  );
