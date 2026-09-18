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
  // Absolute path of the manifest.yaml on the producing node.
  manifest_path?: string | null;
  // Which entry from the producing node's pack_dirs this pack was
  // loaded from — may be a file (precise-file mode) or a directory.
  // Null on rolling upgrades where the node hasn't reconnected yet.
  source_dir?: string | null;
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
  // The snapshot this job belongs to. Present for every workflow-driven
  // job (fan-out parent + shards + regular). Null only for ad-hoc jobs.
  // The canvas ``job_update`` handler filters upserts into
  // ``latestSnapshotJobs`` by this — the identifier is what lets a
  // background-created shard's first ``pending`` frame arrive at the
  // frontend and know it belongs to the current snapshot.
  snapshot_id?: string | null;
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
  // Cardinality flag — true means the port carries an ``arrayed<T>`` (a
  // directory whose immediate subdirs are elements). Non-arrayable packs
  // declare this at manifest time; arrayable packs get it flipped per
  // graph node by ``GraphNode.arrayed_toggle``. See docs/pack-spec.md.
  arrayed?: boolean;
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
  // Populated by the server for directory children so viewers can peek
  // one level into an arrayed<T> layout (``<parent>/<element>/<file>``)
  // without a second summary round trip. Only immediate children; not
  // recursive. Absent for file entries and for directory entries the
  // server chose not to enrich (missing = "don't know", not "empty").
  children?: HandleSummaryEntry[];
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
  // Name of an input port on the SAME pack whose (effective) tags this
  // output mirrors. Set on generic utility packs (``arrayfy`` /
  // ``get-index``) where the element type is decided by the caller's
  // wiring. Null for packs whose output tags are self-contained.
  tags_from?: string | null;
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
  // ⌘/Ctrl+click "Jump to source" target — the pack's core implementation
  // script. Relative paths resolve against the manifest.yaml's own directory;
  // absolute paths are used verbatim. Null for packs that don't declare one;
  // modifier+click then falls back to opening the pack directory.
  source_entry: string | null;
  // Absolute path of the manifest.yaml on the producing node. The
  // plain-click "Jump to source" target — no more ``{source_dir}/
  // {name}@{version}/manifest.yaml`` guessing. Null on legacy nodes.
  manifest_path?: string | null;
  // Which ``pack_dirs`` entry this pack was loaded from on the producing
  // node — may be a file (precise-file mode) or a directory. Lets the
  // palette / inspector show origin so a developer's ad-hoc pack is
  // visually distinguishable from vendored ones. See docs/writing-a-pack.md.
  source_dir?: string | null;
  inputs: Record<string, InputPortSpec>;
  outputs: Record<string, OutputPortSpec>;
  params: Record<string, ParamSpec>;
  // When true, this pack's exec is data-parallel over its arrayed inputs
  // and each node instance gets an "arrayed" checkbox in the Inspector
  // (see docs/pack-spec.md#arrayed-and-arrayable).
  arrayable?: boolean;
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
  // Non-null timestamp when the artifact was tombstoned via
  // DELETE /api/artifacts/{id}. The preview drawer skips the
  // <video>/<img> fetch and renders a "cleaned" placeholder instead
  // of a broken frame. See docs/artifacts.md.
  deleted_ts: number | null;
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
  // Absolute path of the node's primary packs dir (``pack_dirs[0]``).
  // Consumed by the canvas node's "Jump to source" button to resolve
  // the fallback target ``{packs_dir}/{name}@{version}/manifest.yaml``.
  // Null only during a rolling upgrade before this node reconnected
  // on the new protocol. See docs/cobrowser-integration.md#jump-to-source.
  packs_dir: string | null;
  // Full ordered list of pack source directories the node scans. The
  // NodeSettingsDrawer renders + edits this list; adding an entry
  // registers a custom pack source without vendoring the pack into
  // the HoloLab repo. See docs/writing-a-pack.md.
  pack_dirs?: string[];
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
  // ``packs_dir`` is the legacy scalar (== ``pack_dirs[0]``). Modern
  // clients prefer ``pack_dirs``. Both are populated so an older
  // frontend loaded against a newer node still works.
  packs_dir: string | null;
  pack_dirs: string[];
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
  // Structural — when the pack is ``arrayable``, this checkbox promotes
  // every non-arrayed port to arrayed at wire time and fan-outs at
  // dispatch time. Default false leaves pre-arrayed behavior intact.
  // See docs/pack-spec.md#arrayed-and-arrayable.
  arrayed_toggle?: boolean;
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

// GET /api/snapshots/{sid}/deletion-preview — impact of a would-be delete.
// ``blocked`` = a job is still non-terminal; the DELETE endpoint will 409.
// Powers the confirmation modal on the Run History right-click menu so the
// operator sees "N artifacts kept (still shared), K to be removed, B bytes
// freed" before committing.
export interface SnapshotDeletionPreview {
  snapshot_id: string;
  workflow_id: string;
  job_count: number;
  live_jobs: { job_id: string; state: string; algorithm_name: string }[];
  blocked: boolean;
  artifacts: {
    exclusive_count: number;
    shared_count: number;
    exclusive_bytes: number;
  };
  jobs: { exclusive_count: number; shared_count: number };
}

// DELETE /api/snapshots/{sid} — response. ``state: "gone"`` means the row
// was already deleted (idempotent second click / two-tab race). Otherwise
// ``state: "deleted"`` and the counters describe the ref-count outcome.
export interface SnapshotDeleteResult {
  snapshot_id: string;
  workflow_id?: string | null;
  state: "deleted" | "gone";
  jobs_removed?: number;
  jobs_kept_shared?: number;
  artifacts_removed_from_disk?: number;
  artifacts_tombstoned_only?: number;
  artifacts_kept_shared?: number;
  freed_bytes?: number;
}

// GET /api/jobs/{job_id}/log — mirrors ``LogTail`` in gateway/models.py.
// The panel's log viewer asks for a big-but-bounded tail (~10k lines) and
// the server returns them in append order (oldest first) so an auto-scroll
// to the bottom shows the tail of the run — matching what the user would
// have seen if they'd watched stdout live.
export interface LogLine {
  stream: "stdout" | "stderr";
  line: string;
  ts: number;
}

export interface LogTail {
  job_id: string;
  lines: LogLine[];
  total_returned: number;
  truncated: boolean;
}

// GET /api/jobs/{job_id} — full job detail (RecentJobRow + params +
// input_handles + fail_message + fail_exit_code). The log viewer fetches
// this to show ``fail_message`` (the panel row only carries ``fail_reason``).
export interface JobDetail {
  job_id: string;
  workflow_id: string | null;
  snapshot_id: string | null;
  graph_node_id: string | null;
  node_id: string | null;
  algorithm_name: string;
  algorithm_version: string;
  state: string;
  progress: { current: number; total: number } | null;
  fail_reason: string | null;
  fail_exit_code: number | null;
  fail_message: string | null;
  params: Record<string, unknown>;
  input_handles: Record<string, string>;
  created_ts: number;
  updated_ts: number;
}
