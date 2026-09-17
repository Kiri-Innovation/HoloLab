// Types shared with the Python backend. Kept as plain interfaces so we don't
// need a code generator in day 0. If the surface grows, we can generate these
// from the pydantic models.

export interface Envelope<T = unknown> {
  v: number;
  id: string;
  kind: string;
  payload: T;
  ts: number;
}

export interface PackInventoryEntry {
  name: string;
  version: string;
  manifest_hash: string;
}

export interface GpuInfo {
  count: number;
  total_vram_gb?: number;
  name?: string;
  driver_version?: string;
  cuda_version?: string;
}

export interface NodeOnlinePayload {
  node_id: string;
  node_name: string;
  packs: PackInventoryEntry[];
}
export interface NodeOfflinePayload {
  node_id: string;
  reason: string;
}
export interface JobUpdatePayload {
  job_id: string;
  state: string;
  workflow_id: string;
  algorithm_name: string;
  algorithm_version: string;
  // Populated for workflow-driven jobs; null for ad-hoc /api/jobs/run triggers.
  // The frontend uses this to key runtime state by canvas node id.
  graph_node_id: string | null;
  // Only populated on the terminal transition to done — maps
  // output_port_name -> handle_id. Feeds the in-canvas preview drawer.
  output_handles?: Record<string, string> | null;
  progress?: { current: number; total: number } | null;
  fail?: { reason: string; exit_code?: number; message?: string } | null;
}

// GET /api/jobs — same shape as JobUpdatePayload plus workflow bookkeeping.
export interface JobSummary {
  job_id: string;
  workflow_id: string;
  node_id: string | null;
  graph_node_id: string | null;
  algorithm_name: string;
  algorithm_version: string;
  state: string;
  progress: { current: number; total: number } | null;
  fail_reason: string | null;
  created_ts: number;
  updated_ts: number;
}
export interface LogChunkPayload {
  job_id: string;
  lines: string[];
  stream: string;
}

// ---------------------------------------------------------------------------
// Pack catalog (GET /api/pack-catalog)
// ---------------------------------------------------------------------------
//
// Ports are typed by their tag set (the "object type"). Storage form is an
// internal transport hint (dir vs single file) and never affects edge
// compatibility — it exists on the wire only to help downstream nodes
// materialize the payload correctly.

export interface PortSpec {
  tags: string[];
  storage: "dir" | "file";
  description: string | null;
}

export interface OutputPreviewSpec {
  viewer: "splatv" | "video" | "image" | "text" | "video-grid";
  // For single-file viewers this is a literal relative path inside a
  // dir-storage handle. For ``video-grid`` it is an optional glob (e.g.
  // ``*.mp4``) that filters the directory listing; omitted → any common
  // video extension.
  member: string | null;
}

// GET /api/handles/{id}/summary — server-parsed metadata. See
// hololab/gateway/handle_summary.py for the fields per ``kind``.
export interface HandleSummaryEntry {
  name: string;
  is_dir: boolean;
  size_bytes: number | null;
}

export interface HandleSummary {
  handle_id: string;
  kind: "splatv" | "video" | "image" | "text" | "dir" | "unknown";
  tags: string[];
  storage: "dir" | "file";
  size_bytes: number | null;
  proxy_url: string;
  // Producing node's local absolute path (before workspace-root
  // stripping). Used by the Cobrowser "Open in Cocoder" button —
  // ``window.flops.showDocument`` needs the raw path, not a proxy URL.
  // See docs/cobrowser-integration.md.
  absolute_path: string;
  // Discriminated payload. For ``kind: "dir"`` the fields carry
  // ``entries`` / ``entry_count`` / ``total_size_bytes`` / ``truncated``.
  // Other kinds have their own field sets; the frontend only reads the
  // ones it needs.
  fields: {
    entries?: HandleSummaryEntry[];
    entry_count?: number;
    total_size_bytes?: number;
    truncated?: boolean;
    // Passthrough for other kinds.
    [k: string]: unknown;
  };
}

export interface OutputPortSpec extends PortSpec {
  preview: OutputPreviewSpec | null;
}

export interface InputPortSpec extends PortSpec {
  required: boolean;
}

export interface ParamSpec {
  type: string;
  default: unknown;
  description: string | null;
  optional: boolean;
}

export interface CatalogPack {
  name: string;
  version: string;
  manifest_hash: string;
  node_ids: string[]; // compute nodes offering this pack
  description: string | null;
  category: string[];
  docs: string | null;
  // Optional "Jump to source" target — a path (relative to the
  // Kiri4DGS workspace root, i.e. ``packs_dir.parent.parent`` on the
  // node, or absolute). The canvas node's code-icon button opens
  // this in Cocoder; null → falls back to opening the pack's own
  // manifest.yaml. See docs/cobrowser-integration.md#jump-to-source.
  source_entry: string | null;
  inputs: Record<string, InputPortSpec>;
  outputs: Record<string, OutputPortSpec>;
  params: Record<string, ParamSpec>;
}

// GET /api/handles/{id}
export interface HandleInfo {
  handle_id: string;
  node_id: string;
  storage: "dir" | "file";
  tags: string[];
  size_bytes: number | null;
  output_port_name: string | null;
  // Same-origin URL that the browser can GET / <video src=…> against.
  // For dir-storage handles you typically fetch a specific member by
  // appending ``/<member>`` to this URL.
  proxy_url: string;
  // Producing node's local absolute path — see HandleSummary above.
  absolute_path: string;
}

// ---------------------------------------------------------------------------
// Compute nodes (GET /api/nodes)
// ---------------------------------------------------------------------------

export interface ComputeNode {
  node_id: string;
  node_name: string;
  gpu: GpuInfo;
  packs: PackInventoryEntry[];
  connected_ts: number;
  advertised_url: string | null;
  workspace_root: string | null;
  legacy_workspace_roots: string[];
  // Cobrowser integration — Flops device id the operator configured on
  // this compute node's config.yaml. Null when unset. The frontend's
  // "Open in Cocoder" button reads this via ``compute nodes[node_id]``
  // and hands it to ``window.flops.showDocument`` as ``deviceId``. See
  // docs/cobrowser-integration.md.
  flops_executor_id: string | null;
  // Absolute path of the node's packs dir. Consumed by the canvas
  // node's "Jump to source" button to resolve the fallback target
  // ``{packs_dir}/{name}@{version}/manifest.yaml``. Null only during
  // a rolling upgrade before this node reconnected on the new
  // protocol. See docs/cobrowser-integration.md#jump-to-source.
  packs_dir: string | null;
}

// Effective config the node reports via GET /api/nodes/{id}/config. The
// PATCH endpoint accepts a subset of these fields.
export interface NodeEffectiveConfig {
  node_name: string;
  workspace_root: string;
  legacy_workspace_roots: string[];
  file_server_host: string;
  file_server_port: number;
  advertised_url: string | null;
  packs_dir: string;
  // Editable — Cobrowser integration. See docs/cobrowser-integration.md.
  flops_executor_id: string | null;
}

// ---------------------------------------------------------------------------
// Workflow graph (matches hololab/gateway/workflows.py WorkflowGraph)
// ---------------------------------------------------------------------------

export interface GraphNode {
  id: string;
  algorithm_name: string;
  algorithm_version: string;
  position: { x: number; y: number };
  params: Record<string, unknown>;
  assigned_node_id: string | null;
  // Cosmetic (observer-only) field. Name of the output port whose
  // preview drawer is expanded; null when the drawer is closed.
  // Never affects dispatch — see the cosmetic/structural table in
  // docs/workflow-schema.md.
  preview_open?: string | null;
}

export interface GraphEdge {
  id: string;
  source: string;
  sourceHandle: string;
  target: string;
  targetHandle: string;
}

export interface WorkflowGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

// ---------------------------------------------------------------------------
// Run history (GET /api/workflows/{id}/runs, GET /api/snapshots/{id})
// ---------------------------------------------------------------------------

export interface RunSummaryRow {
  snapshot_id: string;
  workflow_id: string;
  created_ts: number;
  node_count: number;
  job_count: number;
  // Map of job state name → count (only states that appear are present).
  state_counts: Record<string, number>;
  // Roll-up used for the row's coloured pip. Same vocabulary as job
  // states plus "pending" for snapshots whose jobs haven't started yet.
  state: string;
  // DB-cheap artifact count for this run: {total, deleted}. Powers the
  // "N missing" chip on the run row. Live liveness (alive/dead) is not
  // stat'd here — the Artifacts page opts into that separately.
  artifact_counts?: { total?: number; deleted?: number };
}

// One job as it appears inside a snapshot detail. Wider than
// RecentJobRow because the read-only run view wants params + input
// handles too, so the user can see "what were the frozen inputs?".
export interface SnapshotJob {
  job_id: string;
  workflow_id: string;
  node_id: string | null;
  graph_node_id: string | null;
  algorithm_name: string;
  algorithm_version: string;
  state: string;
  progress: { current: number; total: number } | null;
  fail_reason: string | null;
  fail_exit_code: number | null;
  fail_message: string | null;
  params: Record<string, unknown>;
  input_handles: Record<string, string>;
  // Named output handles produced by this job, port_name -> handle_id.
  // Null when the job produced no named outputs (matches the JobUpdate
  // WS payload's convention). Powers preview drawers on the read-only
  // snapshot canvas the same way live output_handles do for the draft.
  output_handles: Record<string, string> | null;
  // DB-cheap liveness for each output handle. Values: "deleted" (user
  // cleaned it via the Artifacts page) or "pending" (FS truth not
  // checked). The inspector renders a badge so preview buttons for
  // deleted rows are visibly disabled instead of silently 404ing.
  // Missing key = no handle for that port (older records).
  output_handle_states?: Record<string, string>;
  // Rerun-from-node inheritance marker: non-null means this row's
  // output_handles are inherited from the original job. UI can render
  // "reused" instead of "done".
  reused_from_job_id?: string | null;
  created_ts: number;
  updated_ts: number;
}

export interface SnapshotDetail {
  snapshot_id: string;
  workflow_id: string;
  created_ts: number;
  graph: WorkflowGraph;
  jobs: SnapshotJob[];
}
