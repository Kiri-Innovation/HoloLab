// Custom xyflow node for one algorithm-pack instance.
//
// The node's shape is manifest-driven: inputs on the left, outputs on the
// right, each rendered as a coloured dot that carries the port's tags via
// dataset attributes. The App-level onConnect validator reads those tags to
// enforce tag compatibility at edge-drawing time.

import { useEffect, useMemo, useState } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import type {
  CatalogPack,
  InputPortSpec,
  OutputPortSpec,
  OutputPreviewSpec,
  PortSpec,
} from "../wire";
import { effectivePortArrayed, effectivePortDimLabels, firstTagColour } from "../tags";
import { useCanvasContext } from "./CanvasContext";
import type { NodeStaleness } from "./staleness";
import { CopyRefButton } from "./CopyRefButton";
import { OpenArtifactLocationButton } from "./OpenArtifactLocationButton";
import { OpenInCocoderButton } from "./OpenInCocoderButton";
import { OpenSourceButton } from "./OpenSourceButton";
import { BasicInfoPreview, Preview } from "./previews";
import { PreviewPlaceholder } from "./PreviewPlaceholder";

function inputTitle(portName: string, spec: InputPortSpec): string {
  const parts = [`port: ${portName}`, `tags: ${spec.tags.join(", ")}`];
  if (!spec.required) parts.push("optional");
  if (spec.description) parts.push(spec.description);
  return parts.join(" · ");
}

function outputTitle(portName: string, spec: PortSpec): string {
  const parts = [`port: ${portName}`, `tags: ${spec.tags.join(", ")}`];
  if (spec.description) parts.push(spec.description);
  return parts.join(" · ");
}

// Snapshot of the latest job run for one blueprint node. Driven by WS
// ``job_update`` events keyed on ``graph_node_id`` in App.tsx.
export interface NodeRuntime {
  state: string; // "pending" | "assigned" | "running" | "done" | "failed" | "cancelled" | "orphaned"
  progress?: { current: number; total: number } | null;
  fail_reason?: string | null;
  job_id?: string;
  // Aggregated timing across the newest generation's parent + shards.
  // ``started_ts`` = earliest start (falls back to created_ts per row).
  // ``updated_ts`` = latest update. Nullable so aggregator output for a
  // brand-new-and-unrun graph_node stays a plain state string.
  started_ts?: number | null;
  updated_ts?: number | null;
}

// One resolved shard preview — populated while an arrayed node's fanout is
// still in progress. Each done shard registers its own output_handles map
// keyed by port; App resolves each to a proxy_url. The drawer switches to
// partial mode (ArrayedPaginator ``partial={…}``) when the aggregate parent
// hasn't landed a target in ``previews`` yet but this array is non-empty.
export interface PreviewShard {
  element_id: string;
  handle_id: string;
  proxy_url: string;
  storage: "dir" | "file";
}

// One resolved preview target — the App fetches this via the /api/handles/{id}
// lookup after a done job_update carries an output_handles map. Kept on
// node data so re-renders don't re-fetch. See App.tsx for the flow.
export interface PreviewTarget {
  port_name: string;
  handle_id: string;
  // Compute node id that produced this handle. The Cobrowser "Open in
  // Cocoder" button reads the compute node's ``flops_executor_id``
  // (from the ``computeNodesById`` map on the enclosing
  // AlgorithmNodeData) to know which machine to target.
  node_id: string;
  proxy_url: string;
  storage: "dir" | "file";
  // Producing node's local absolute path. Fed straight into
  // ``window.flops.showDocument`` by the Cobrowser "Open in Cocoder"
  // button — a proxy URL wouldn't do because Cobrowser opens LOCAL
  // files. See docs/cobrowser-integration.md.
  absolute_path: string;
  // Mirrors HandleInfo.deleted_ts !== null. When true the on-disk
  // artifact was tombstoned; render the "cleaned" placeholder + Run
  // button instead of a <video>/<img> that would 404. See
  // docs/artifacts.md.
  deleted: boolean;
  // Runtime-resolved tag list from the gateway's handle book (i.e.
  // after ``tags_from`` propagation — a ``regroup.out`` handle wired
  // from an ``image`` source lands here as ``["image"]``, not the
  // manifest's raw ``["any"]``). Preview routing prefers this over the
  // static ``port.tags`` from the catalog so the viewer picked matches
  // the handle's actual data class, not its producing pack. Empty
  // array = handle carried no tags (shouldn't happen for real outputs
  // but tolerated).
  tags: string[];
  // Backend-resolved preview spec: pack's explicit ``preview:`` if any,
  // otherwise the tag-inferred default (``TAG_VIEWER_REGISTRY`` on the
  // gateway). Null when neither matched. Frontend uses this as the
  // single source of truth for drawer promotion — a generic port whose
  // catalog spec has ``preview: null`` still gets a viewer as long as
  // the runtime handle resolved a tag with a registered viewer.
  preview: OutputPreviewSpec | null;
  // Producing port's declared ``dim_labels`` (from the manifest as
  // returned by the backend, not the catalog's static value). Length
  // = arrayed depth. Prefer this over the catalog's ``dim_labels`` on
  // generic ports whose static value is placeholder strings
  // (``["", ""]``). Null when the backend didn't have a producing port.
  dim_labels: string[] | null;
  // Per-dim element counts, outer-first. Null for scalar / file-storage
  // or when the walk couldn't produce a uniform shape.
  dim_sizes: number[] | null;
}

export interface AlgorithmNodeData extends Record<string, unknown> {
  pack: CatalogPack;
  assigned_node_id: string | null;
  runtime?: NodeRuntime;
  // Resolved preview URLs for each output port that has a preview
  // declaration AND has produced a handle. Key is the output port name.
  previews?: Record<string, PreviewTarget>;
  // Per-port list of shard previews, for arrayed nodes mid-fanout.
  // Populated as each shard completes; consumed by the drawer when the
  // aggregate target isn't in ``previews`` yet.
  previewShards?: Record<string, PreviewShard[]>;
  // Frontend-only: which preview drawer is expanded, if any. The node
  // grows a slot below its body when set. ``null`` = collapsed.
  previewOpen?: string | null;
  // Optional per-node toggle. When supplied, the expand caret calls this
  // callback instead of dispatching PREVIEW_TOGGLE_EVENT — used by the
  // read-only snapshot canvas so its toggles stay local to that view
  // instead of leaking into App's draft-scoped preview state.
  onPreviewToggle?: (port_name: string | null) => void;
  // Read-only rendering — snapshot canvas sets this so the drawer's
  // "run this node" button doesn't show (dispatching from a frozen
  // past snapshot has no clean meaning; the user re-runs from the
  // draft). Draft canvas leaves it undefined.
  readOnly?: boolean;
  // Result-staleness badge state — driven by canvas/staleness.ts against
  // the latest snapshot graph. ``null`` (or omitted) means fresh /
  // in-flight — the existing status dot already tells the story and no
  // amber badge is drawn. Only ever populated on the draft canvas; the
  // snapshot canvas leaves this undefined so history mode stays quiet.
  staleness?: NodeStaleness | null;
}

// NOTE: ``workflow_id`` and ``computeNodesById`` are NOT on the node data.
// They live in ``CanvasContext`` because they're canvas-wide, not per-node —
// baking them into ``data`` caused xyflow's ``adoptUserNodes`` to re-init
// every node whenever a WS event refreshed compute nodes, occasionally
// clearing ``handleBounds`` mid-hydration and dropping edges.

// Flat, restrained palette. Same colours reused across the AlgorithmNode
// badge, the Jobs panel row, and the workflow-run summary counter so a
// glance across the UI is coherent.
export const STATE_COLOURS: Record<string, string> = {
  pending: "var(--status-pending)",
  assigned: "var(--status-assigned)",
  running: "var(--status-running)",
  done: "var(--status-done)",
  failed: "var(--status-failed)",
  cancelled: "var(--status-cancelled)",
  orphaned: "var(--status-orphaned)",
};

export function stateColour(state: string | undefined): string {
  return (state && STATE_COLOURS[state]) || "var(--status-pending)";
}

// Compact duration used in the node-card footer. Kept independent of
// RecentJobsPanel's ``formatElapsed`` so the two views can tune their
// resolution separately (panel = 1s/1m/1h buckets; card wants seconds
// distinguishable up to 60, minute+second between 1m and 60m). Truncates
// negatives to 0s so a clock-skew glitch never renders "-3s".
function formatCardDuration(startTs: number, endTs: number): string {
  const secs = Math.max(0, Math.round(endTs - startTs));
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) {
    const rem = secs % 60;
    return rem === 0 ? `${mins}m` : `${mins}m ${rem}s`;
  }
  const hrs = Math.floor(mins / 60);
  const rmin = mins % 60;
  return rmin === 0 ? `${hrs}h` : `${hrs}h ${rmin}m`;
}

// Coarse "X 分钟前 / X 小时前 / 刚刚" label for the footer's completion
// time. Deliberately vague past an hour: the operator cares about
// "recent" vs "hours ago" vs "yesterday", not minute-precision. All in
// zh-CN because the rest of the UI copy already is.
function formatRelativeTime(ts: number, now: number): string {
  const diff = Math.max(0, Math.round(now - ts));
  if (diff < 5) return "刚刚";
  if (diff < 60) return `${diff}秒前`;
  const mins = Math.floor(diff / 60);
  if (mins < 60) return `${mins}分钟前`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}小时前`;
  const days = Math.floor(hrs / 24);
  if (days < 30) return `${days}天前`;
  return "很久以前";
}

const IN_FLIGHT_STATES_FOR_TICK = new Set([
  "running",
  "assigned",
  "pending",
]);

/** Ticking wall-clock second-counter — only mounted while the node is
 *  in-flight so idle cards don't force a full-canvas re-render every
 *  second. Returns ``now`` in seconds (matches the backend timestamp
 *  domain used across the codebase). */
function useLiveNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(id);
  }, [active]);
  return now;
}

const DOT: React.CSSProperties = {
  borderRadius: "var(--radius-pill)",
};

// Applied on top of DOT for ports whose effective ``arrayed`` is true —
// a subtle "target" halo (surface-coloured inner ring + accent outer
// ring) so a glance at the canvas tells you which edges carry
// arrayed<T> vs scalar values. Reads cleanly against both light and
// dark themes because both ring colours come from CSS vars.
const ARRAYED_DOT_OVERLAY: React.CSSProperties = {
  boxShadow: "0 0 0 2px var(--surface-2), 0 0 0 3.5px var(--text-muted)",
};

// One I/O row — dot + name, sized so N inputs and M outputs each flow
// independently without cross-alignment.
const PORT_ROW: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  fontSize: "var(--fs-xs)",
  padding: "var(--space-1) 0",
  minHeight: 20,
  color: "var(--text-body)",
  lineHeight: "var(--lh-ui)",
};

const NODE_WIDTH = 220;
const NODE_WIDTH_EXPANDED = 340; // wider slot so previews have room to breathe

// Outer frame dimensions for ``arrayed_toggle`` nodes. The frame's job is
// to say "this node fans out over its array input" at a glance without
// stealing header real-estate from the pack name. Sides+bottom stay thin
// so the frame reads as a hairline; the top band is a full label row.
// Handles are pushed outward by (FRAME_INSET) so their centres sit on
// the frame's outer border rather than the inner card's. Kept as consts
// in one place so the CSS override below and the JSX layout can't drift.
const FRAME_PAD_SIDE = 5;
const FRAME_PAD_TOP = 20;
const FRAME_BORDER = 1;

// An expandable output = a port the operator can open the drawer on.
// Two cases:
//
//   * ``kind: "viewer"`` — the pack declared a preview viewer. The
//     caret shows even without a live handle so the "尚未运行 / 产物已被清理"
//     placeholders (see PreviewPlaceholder) + the in-place Run button
//     still work.
//   * ``kind: "info"``  — no preview declared but the last run resolved
//     a handle. The drawer shows BasicInfoPreview (path, size, contents,
//     Open-in-Cocoder) so operators aren't blind to what nodes without
//     a bespoke viewer (stg-train, regroup-by-frame, colmap-assemble, …)
//     just produced.
//
// A port with both a preview spec AND a target keeps ``kind: "viewer"``
// — the viewer's own header already carries the same Open-in-Cocoder
// + CopyRef affordances BasicInfoPreview surfaces.
type ExpandKind = "viewer" | "info";
interface ExpandablePort {
  name: string;
  spec: OutputPortSpec;
  target: PreviewTarget | null;
  kind: ExpandKind;
}

// Tags for which the frontend intercepts Preview() dispatch and picks
// its own viewer regardless of what the backend's tag_viewers.py
// registered (or didn't). Kept in one place so a new frontend-driven
// tag family only has to touch two spots: this set + the intercept
// clause in previews.tsx's Preview() function. Rolling upgrades where
// the running gateway hasn't ingested a new tag_viewers.py entry
// yet — like ``colmap-cams`` at the time of writing — still get the
// caret because this promotion looks at the port's ``tags`` directly.
const FRONTEND_VIEWER_TAGS = new Set<string>([
  "frame_sequence",
  // Structural rename — see previews.tsx for the routing pair. Both
  // tags accepted so a rolling migration doesn't drop the caret.
  "image_sequence",
  "colmap-cams",
  // ``point-cloud`` is the 2026-09-22 type-system refactor's canonical
  // tag for triangulated points; ``colmap-points`` was the retired
  // predecessor (colmap-triangulate@0.1.0). Both route to
  // ColmapPointsPreview in previews.tsx.
  "point-cloud",
  "colmap-points",
  // ``colmap-folder`` is the 2026-09-22 refactor's canonical tag for a
  // self-contained COLMAP folder (sparse/0/ + optional images/), emitted
  // by merge-colmap@0.2.0/0.3.0 (which also carries ``colmap`` as a
  // legacy compat alias so stg-train@0.2.0 accepts the edge without a
  // manifest bump). ``colmap-frame`` is the retired
  // colmap-triangulate@0.3.0 tag. All three route to ColmapFramePreview.
  "colmap-folder",
  "colmap",
  "colmap-frame",
  // Rig chain — iframe/ColmapUtil previews. rig_extrinsics is the
  // post-BA rig model; rig_points4d is the per-group triangulation.
  "rig_extrinsics",
  "rig_points4d",
  // Exposure-timeline diagnostic figure (moved from rig-frame-extraction to
  // rig-temporal-grouping@0.1.1, where the buckets are actual data).
  "rig_timeline",
  // rig-capture-source's per-alias camera dirs — 7-tile video grid.
  "rig_capture",
  // Per-alias JPEG tree from rig-frame-extraction / rig-temporal-grouping.
  // Rendered as arrayed<frame_sequence> via NestedFrameSequencePreview.
  "rig_frames",
  // Per-group directory tree from rig-temporal-grouping@0.1.2 — element =
  // one time bucket, children = the participating aliases' JPEGs. Same
  // NestedFrameSequencePreview shape (element/image tree).
  "rig_frames_grouped",
]);

function hasFrontendViewerTag(tags: string[] | undefined): boolean {
  if (!tags) return false;
  for (const t of tags) {
    if (FRONTEND_VIEWER_TAGS.has(t)) return true;
  }
  return false;
}

function expandableOutputs(
  pack: CatalogPack,
  previews: Record<string, PreviewTarget> | undefined,
): ExpandablePort[] {
  const out: ExpandablePort[] = [];
  for (const [name, spec] of Object.entries(pack.outputs)) {
    const target = previews?.[name] ?? null;
    // A port becomes a viewer when EITHER the pack pre-declared one, OR
    // the pack's manifest tags map to a frontend intercept, OR the
    // runtime handle resolved a preview / a frontend-viewer tag. The
    // last clause is what makes generic ports (``regroup.out``:
    // ``tags: [any]`` + ``preview: null`` at catalog time) still open
    // as a viewer once their runtime tag lands as ``["image"]`` — the
    // backend's ``tag_viewers`` resolver has already turned that into a
    // real preview spec on ``target.preview``.
    const staticViewer = spec.preview || hasFrontendViewerTag(spec.tags);
    const runtimeViewer = Boolean(
      target && (target.preview || hasFrontendViewerTag(target.tags)),
    );
    if (staticViewer || runtimeViewer) {
      out.push({ name, spec, target, kind: "viewer" });
    } else if (target) {
      out.push({ name, spec, target, kind: "info" });
    } else {
      // No preview spec and no handle yet — include anyway so the caret
      // is always visible for nodes with outputs, and clicking it shows
      // the "尚未运行" placeholder + Run button before the first run.
      out.push({ name, spec, target: null, kind: "info" });
    }
  }
  return out;
}

// Toggle event — the App listens for it and updates its state, which flows
// back into node.data.previewOpen. Keeps AlgorithmNode a plain read-only
// renderer without needing a context or callback prop drilled through
// xyflow's nodeTypes registry.
export const PREVIEW_TOGGLE_EVENT = "hololab-preview-toggle";

export interface PreviewToggleDetail {
  graph_node_id: string;
  port_name: string | null; // null = close whatever's open
}

function dispatchToggle(graph_node_id: string, port_name: string | null): void {
  window.dispatchEvent(
    new CustomEvent<PreviewToggleDetail>(PREVIEW_TOGGLE_EVENT, {
      detail: { graph_node_id, port_name },
    }),
  );
}

// Same event-bridge pattern as PREVIEW_TOGGLE_EVENT — App listens and
// calls dispatchNode(workflow_id, graph_node_id). The event carries a
// resolver so the placeholder can await success/failure and show a
// local status without threading a callback prop through xyflow's
// nodeTypes registry. Only fires from the draft canvas — snapshot
// canvas sets ``readOnly`` so the button never renders.
export const RUN_NODE_EVENT = "hololab-run-node";

export interface RunNodeDetail {
  graph_node_id: string;
  resolve: () => void;
  reject: (msg: string) => void;
  // Flipped to true by the App-level listener the moment it accepts the
  // event. Lets ``dispatchRunNode`` distinguish "no listener registered"
  // (App tree not mounted, or an unmount race) from "listener accepted
  // but the fetch is still in flight". Without it, a click before the
  // useEffect that registers ``onRun`` runs would leave the button
  // pinned in "…" forever because resolve/reject would never fire.
  handled?: boolean;
}

// Hard ceiling on how long we'll wait for the App-level listener to
// resolve/reject. Covers the "dispatchNode() promise never settles"
// deadlock — a hung fetch, an unresponsive gateway, or a listener that
// forgets to call the callbacks. 20s is generous vs. the P99 dispatch
// (<200ms) and short enough that a user staring at "…" gives up before
// this fires.
const DISPATCH_TIMEOUT_MS = 20_000;

function dispatchRunNode(graph_node_id: string): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    let settled = false;
    let timer: number | null = null;
    const settle = (fn: () => void) => {
      if (settled) return;
      settled = true;
      if (timer !== null) window.clearTimeout(timer);
      fn();
    };
    const detail: RunNodeDetail = {
      graph_node_id,
      resolve: () => settle(resolve),
      reject: (msg) => settle(() => reject(new Error(msg))),
      handled: false,
    };
    timer = window.setTimeout(
      () =>
        settle(() =>
          reject(new Error(`dispatch timed out after ${DISPATCH_TIMEOUT_MS / 1000}s`)),
        ),
      DISPATCH_TIMEOUT_MS,
    );
    window.dispatchEvent(new CustomEvent<RunNodeDetail>(RUN_NODE_EVENT, { detail }));
    // Synchronous window.dispatchEvent has already run every listener by
    // the time we get here. If none flipped ``handled`` the App-level
    // ``onRun`` isn't wired — better to surface that as a clear "reload
    // the page" error than leave the button spinning until the 20s
    // timeout fires.
    if (!detail.handled) {
      settle(() =>
        reject(new Error("no run handler registered — reload the page")),
      );
    }
  });
}

export function AlgorithmNode({ id, data, selected }: NodeProps) {
  const {
    pack,
    assigned_node_id,
    runtime,
    previews,
    previewShards,
    previewOpen,
    onPreviewToggle,
    readOnly,
    staleness,
    arrayed_toggle,
  } = data as AlgorithmNodeData & { arrayed_toggle?: boolean };
  const { workflow_id: workflowId, computeNodesById, hydrating } = useCanvasContext();
  const arrayedOn = Boolean(arrayed_toggle && pack.arrayable);
  const inputEntries = Object.entries(pack.inputs);
  const outputEntries = Object.entries(pack.outputs);
  const runState = runtime?.state;
  const runColour = stateColour(runState);
  const expandables = expandableOutputs(pack, previews);
  // Expand a port so long as it is expandable (viewer-backed OR a
  // handle exists). The drawer decides at render time whether to show
  // the real Preview, the basic-info panel, or a placeholder.
  const expandableNames = useMemo(
    () => new Set(expandables.map((p) => p.name)),
    [expandables],
  );
  const expanded =
    previewOpen && expandableNames.has(previewOpen) ? previewOpen : null;
  const currentPreview =
    expanded ? expandables.find((p) => p.name === expanded) ?? null : null;
  const width =
    expanded && currentPreview?.kind === "viewer"
      ? NODE_WIDTH_EXPANDED
      : NODE_WIDTH;
  const runInFlight =
    runState === "pending" || runState === "assigned" || runState === "running";

  const [runClickPending, setRunClickPending] = useState(false);
  const [runErrorMsg, setRunErrorMsg] = useState<string | null>(null);

  const handleRunClick = async () => {
    if (runInFlight || runClickPending) return;
    setRunClickPending(true);
    setRunErrorMsg(null);
    try {
      await dispatchRunNode(id);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setRunErrorMsg(msg);
      // 8 s so the longer messages ("dispatch timed out after 20s",
      // "no run handler registered — reload the page") stay long enough
      // for a user to actually read and act on. Short 4-word errors
      // still clear on their own — this is a maximum, not a minimum.
      setTimeout(() => setRunErrorMsg(null), 8000);
    } finally {
      setRunClickPending(false);
    }
  };

  const assignedComputeNode =
    assigned_node_id ? computeNodesById?.[assigned_node_id] ?? null : null;
  const assignedLabel = assignedComputeNode
    ? assignedComputeNode.node_name
    : assigned_node_id
      ? assigned_node_id.slice(0, 6)
      : "not assigned";
  const progressLabel =
    runtime?.progress && runtime.progress.total > 0
      ? `${runtime.progress.current}/${runtime.progress.total}`
      : null;
  const statusTitle = runtime?.fail_reason
    ? `${runState} · ${runtime.fail_reason}`
    : runState || "no run yet";
  // Subtle top-border tint on failed nodes so a glance at the canvas
  // shows which cards need attention without dedicating header space
  // to a text pill.
  const failedTint = runState === "failed";

  // Footer "跳转产物位置" affordance — jumps to the on-disk location of
  // this node's produced artifacts. Uses the FIRST output port that
  // resolved a PreviewTarget (i.e. persistently carries the last run's
  // handle, even in stale / mid-rerun states). Ports without artifacts
  // are skipped so the affordance never lies. Multi-output cases still
  // work per-port via the drawer's own Open-in-Cocoder buttons.
  const firstArtifactTarget = (() => {
    if (!previews) return null;
    for (const name of Object.keys(pack.outputs)) {
      const t = previews[name];
      if (t && !t.deleted) return t;
    }
    return null;
  })();
  const artifactComputeNode = firstArtifactTarget
    ? computeNodesById?.[firstArtifactTarget.node_id] ?? assignedComputeNode
    : null;

  const card = (
    <div
      className="hololab-node"
      style={{
        width,
        background: "var(--surface-2)",
        border: `1px solid ${selected ? "var(--accent)" : failedTint ? "var(--status-failed)" : "var(--border-strong)"}`,
        borderRadius: "var(--radius-md)",
        boxShadow: arrayedOn ? "none" : "var(--rf-node-shadow)",
        fontFamily: "var(--font-sans)",
        color: "var(--text-body)",
        position: "relative",
        transition: "width 120ms ease-out, box-shadow var(--dur-fast) var(--ease)",
      }}
      data-hololab-node="algorithm"
      data-state={runState || ""}
      data-preview-open={expanded || ""}
    >
      {/* HEADER — status dot + pack name (ellipsis) + action icons */}
      <div
        style={{
          padding: "var(--space-2) var(--space-3)",
          borderBottom: "1px solid var(--border)",
          background: "var(--surface-raised)",
          borderRadius: "var(--radius-md) var(--radius-md) 0 0",
          display: "flex",
          alignItems: "center",
          gap: "var(--space-2)",
          minHeight: 32,
          position: "relative",
        }}
      >
        <span
          data-hl-node-status={runState || "idle"}
          title={statusTitle}
          style={{
            flex: "0 0 auto",
            width: 8,
            height: 8,
            borderRadius: "var(--radius-pill)",
            background: runState ? runColour : "var(--border-strong)",
            boxShadow: runState === "running"
              ? "0 0 0 2px color-mix(in srgb, var(--status-running) 25%, transparent)"
              : "none",
          }}
        />
        {!readOnly && staleness && staleness.kind !== "inflight_old_params" && (
          <span
            data-hl-node-stale={staleness.kind}
            title={`结果陈旧 · ${staleness.title}${staleness.kind === "self_dirty" ? " · 点击 ▶ 从此节点重跑" : ""}`}
            style={{
              flex: "0 0 auto",
              width: 8,
              height: 8,
              borderRadius: "var(--radius-pill)",
              // Filled amber = this node's own config diverged. Hollow
              // ring = only the inputs upstream changed; the operator
              // usually wants to jump to the upstream node first.
              background: staleness.kind === "self_dirty" ? "var(--warning)" : "transparent",
              border: staleness.kind === "upstream_dirty" ? "1.5px solid var(--warning)" : "none",
              boxSizing: "border-box",
            }}
          />
        )}
        {!readOnly && staleness?.kind === "inflight_old_params" && (
          // A chip (not a dot) because the case is louder than "your
          // draft has drifted from the last snapshot" — the operator's
          // running command is *right now* using params they don't see
          // in the config drawer. See canvas/staleness.ts for the
          // motivating incident. Filled warning chip with the literal
          // text ``旧参数`` so the meaning doesn't rely on iconography;
          // the tooltip carries the specific reasons and the job id.
          <span
            data-hl-node-stale="inflight_old_params"
            title={staleness.title}
            style={{
              flex: "0 0 auto",
              display: "inline-flex",
              alignItems: "center",
              gap: 3,
              padding: "0 var(--space-1)",
              height: 16,
              borderRadius: "var(--radius-pill)",
              background: "var(--warning-soft)",
              border: "1px solid var(--warning)",
              color: "var(--warning)",
              fontSize: "var(--fs-xs)",
              fontWeight: "var(--fw-semibold)",
              lineHeight: 1,
              whiteSpace: "nowrap",
              boxSizing: "border-box",
            }}
          >
            <span aria-hidden="true" style={{ fontWeight: "var(--fw-bold)" }}>!</span>
            旧参数
          </span>
        )}
        <div
          title={`${pack.name} v${pack.version} · ${assignedLabel}`}
          style={{
            flex: 1,
            minWidth: 0,
            display: "flex",
            flexDirection: "column",
            justifyContent: "center",
            gap: 1,
          }}
        >
          <span
            style={{
              fontWeight: "var(--fw-semibold)",
              fontSize: "var(--fs-md)",
              color: "var(--text)",
              lineHeight: "var(--lh-tight)",
              letterSpacing: "-0.005em",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {pack.name}
          </span>
          <span
            style={{
              fontSize: "var(--fs-micro)",
              fontFamily: "var(--font-mono)",
              color: assigned_node_id ? "var(--text-muted)" : "var(--text-subtle)",
              lineHeight: 1,
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {assignedLabel} · v{pack.version}
          </span>
        </div>
        <div
          style={{
            flex: "0 0 auto",
            display: "flex",
            alignItems: "center",
            gap: "var(--space-1)",
          }}
        >
          <OpenSourceButton
            pack={pack}
            computeNode={
              assignedComputeNode ||
              (pack.node_ids[0] && computeNodesById?.[pack.node_ids[0]]) ||
              null
            }
          />
          {workflowId && (
            <CopyRefButton
              kind="graph-node"
              id={`${workflowId}/${id}`}
              comment={`${pack.name}${runState ? ` · ${runState}` : ""}`}
              size="xs"
            />
          )}
          {!readOnly && (
            <RunButton
              pending={runClickPending}
              inFlight={runInFlight}
              stale={staleness?.kind === "self_dirty"}
              onClick={(e) => {
                e.stopPropagation();
                handleRunClick();
              }}
            />
          )}
        </div>
        {runErrorMsg && (
          <div
            style={{
              position: "absolute",
              top: "100%",
              right: "var(--space-2)",
              zIndex: 10,
              marginTop: 2,
              background: "var(--status-failed)",
              color: "#fff",
              fontSize: "var(--fs-xs)",
              padding: "2px var(--space-2)",
              borderRadius: "var(--radius-sm)",
              maxWidth: 200,
              wordBreak: "break-word",
              pointerEvents: "none",
            }}
          >
            {runErrorMsg}
          </div>
        )}
      </div>

      {/* BODY — inputs left column, outputs right column, independent stacks */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          columnGap: "var(--space-2)",
          padding: "var(--space-2) var(--space-3)",
          minHeight: 24,
        }}
      >
        <div style={{ display: "flex", flexDirection: "column" }}>
          {inputEntries.map(([portName, spec]) => {
            const isArrayed = effectivePortArrayed(
              spec.arrayed,
              pack.arrayable,
              arrayed_toggle,
            );
            return (
              <div key={`in-${portName}`} style={{ ...PORT_ROW, position: "relative" }}>
                <Handle
                  type="target"
                  position={Position.Left}
                  id={portName}
                  style={{
                    ...DOT,
                    background: firstTagColour(spec.tags),
                    ...(isArrayed ? ARRAYED_DOT_OVERLAY : {}),
                  }}
                  data-tags={spec.tags.join(",")}
                  data-required={spec.required ? "1" : "0"}
                  data-hl-arrayed-port={isArrayed ? "" : undefined}
                />
                <span
                  title={inputTitle(portName, spec)}
                  style={{
                    marginLeft: 10,
                    color: spec.required
                      ? "var(--text-body)"
                      : "var(--text-subtle)",
                    fontFamily: "var(--font-mono)",
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    minWidth: 0,
                  }}
                >
                  {portName}
                  {!spec.required && (
                    <span style={{ color: "var(--text-subtle)" }}>?</span>
                  )}
                </span>
              </div>
            );
          })}
        </div>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end" }}>
          {outputEntries.map(([portName, spec]) => {
            const isArrayed = effectivePortArrayed(
              spec.arrayed,
              pack.arrayable,
              arrayed_toggle,
            );
            return (
              <div
                key={`out-${portName}`}
                style={{ ...PORT_ROW, position: "relative", justifyContent: "flex-end" }}
              >
                <span
                  title={outputTitle(portName, spec)}
                  style={{
                    marginRight: 10,
                    color: "var(--text-body)",
                    fontFamily: "var(--font-mono)",
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    minWidth: 0,
                    textAlign: "right",
                  }}
                >
                  {portName}
                </span>
                <Handle
                  type="source"
                  position={Position.Right}
                  id={portName}
                  style={{
                    ...DOT,
                    background: firstTagColour(spec.tags),
                    ...(isArrayed ? ARRAYED_DOT_OVERLAY : {}),
                  }}
                  data-tags={spec.tags.join(",")}
                  data-hl-arrayed-port={isArrayed ? "" : undefined}
                />
              </div>
            );
          })}
        </div>
      </div>

      {/* FOOTER — run result: elapsed + relative-time + progress + actions.
          Header carries identity (compute-node · version); footer is
          reserved for the latest run's outcome. */}
      <div
        style={{
          padding: "var(--space-1) var(--space-3)",
          borderTop: "1px solid var(--border-subtle)",
          background: "var(--surface)",
          borderRadius: "0 0 var(--radius-md) var(--radius-md)",
          display: "flex",
          alignItems: "center",
          gap: "var(--space-2)",
          fontSize: "var(--fs-micro)",
          color: "var(--text-muted)",
          fontFamily: "var(--font-mono)",
          minHeight: 20,
        }}
      >
        <FooterRunSummary
          runtime={runtime}
          runColour={runColour}
          statusTitle={statusTitle}
        />
        {progressLabel && (
          <span
            style={{
              color: runColour,
              fontVariantNumeric: "tabular-nums",
              whiteSpace: "nowrap",
            }}
            title={statusTitle}
          >
            {progressLabel}
          </span>
        )}
        {/* Collapsed-only per user's request ("在没展开的时候"). When the
            drawer is open, per-port Open-in-Cocoder buttons in the
            drawer header already cover this affordance. */}
        {!expanded && firstArtifactTarget && (
          <OpenArtifactLocationButton
            path={firstArtifactTarget.absolute_path}
            computeNode={artifactComputeNode}
          />
        )}
        {expandables.length > 0 && (
          <PreviewCaret
            expanded={Boolean(expanded)}
            onClick={() => {
              const next = expanded ? null : expandables[0].name;
              if (onPreviewToggle) {
                onPreviewToggle(next);
              } else {
                dispatchToggle(id, next);
              }
            }}
          />
        )}
      </div>

      {expanded && currentPreview && (
        <div
          // ``nodrag``: keep pointer gestures inside the drawer local —
          // clicking a video tile, dragging the shared playback bar's
          // scrubber, etc. must not double-fire as a node-move. Any
          // preview element that OWNS its own drag (the scrubber
          // specifically) also carries its own ``nodrag`` for
          // defence-in-depth.
          //
          // Wheel is NOT blocked here: earlier builds stopped the
          // wheel event at the drawer wrapper so the canvas couldn't
          // zoom over an open preview, but that turned the drawer
          // into a wheel black hole. The user wants the natural
          // behavior back — wheel over the drawer zooms the canvas
          // like it does anywhere else, unless a specific descendant
          // opts out via xyflow's ``nowheel`` (none in this file yet).
          className="nodrag"
          style={{
            padding: 8,
            borderTop: "1px solid var(--border)",
            background: "var(--inverse-surface)",
            borderRadius: "0 0 var(--radius-md) var(--radius-md)",
          }}
        >
          {(() => {
            const { name, spec: port, target, kind: expandKind } = currentPreview;
            // Live drawer body when the handle is present and not
            // tombstoned. Both viewer and basic-info kinds share the
            // same header (pack · port + Open-in-Cocoder + CopyRef) so
            // the operator gets the same actions regardless of whether
            // the pack shipped a viewer. Only the body under the header
            // differs: real Preview vs BasicInfoPreview.
            const showLive = target !== null && !target.deleted;
            if (showLive && target) {
              const header = (
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    marginBottom: 6,
                    fontSize: 10,
                    color: "var(--inverse-muted)",
                  }}
                >
                  <span style={{ flex: 1 }}>
                    {pack.name} · {name}
                  </span>
                  <OpenInCocoderButton
                    path={target.absolute_path}
                    computeNode={
                      computeNodesById?.[target.node_id] ?? null
                    }
                    onDark
                  />
                  <CopyRefButton
                    kind="handle"
                    id={target.handle_id}
                    comment={`${pack.name} · ${name} output`}
                    size="xs"
                    onDark
                  />
                </div>
              );
              if (expandKind === "viewer") {
                return (
                  <>
                    {header}
                    <Preview
                      // Prefer the runtime-resolved preview from the
                      // handle response over the catalog's static
                      // declaration. A ``regroup.out`` port declares
                      // ``preview: null`` in the catalog (its element
                      // type is only known at wire time), but the
                      // backend's ``tag_viewers`` resolver turns the
                      // resolved runtime tag into a real spec on
                      // ``target.preview``. When neither is set the
                      // tag intercepts in Preview() still handle
                      // frontend-driven viewers by tag alone.
                      spec={target.preview ?? port.preview ?? undefined}
                      baseUrl={target.proxy_url}
                      storage={target.storage}
                      handleId={target.handle_id}
                      absolutePath={target.absolute_path}
                      producingNode={
                        computeNodesById?.[target.node_id] ?? null
                      }
                      // Tag-driven viewer routing: prefer the runtime
                      // handle's tags (resolved via ``tags_from`` at
                      // handle_register — see gateway/app.py) over the
                      // catalog's static declaration. A ``regroup.out``
                      // handle wired from an ``image`` source arrives
                      // here as ``["image"]``, matching the image
                      // preview; the raw ``port.tags`` would be
                      // ``["any"]`` and skip every intercept in
                      // previews.tsx. Fall back to ``port.tags`` for
                      // legacy handles registered before the resolver
                      // shipped (tags list still populated but never
                      // rewritten).
                      tags={target.tags.length > 0 ? target.tags : port.tags}
                      arrayed={effectivePortArrayed(
                        port.arrayed,
                        pack.arrayable,
                        arrayed_toggle,
                      )}
                      // dim_labels: prefer the runtime value from the
                      // backend (which read the producing port's real
                      // manifest) over the catalog's static value. A
                      // generic port declares placeholder labels
                      // (``["", ""]``) so the paginator can't infer
                      // depth from them alone; the backend fills in
                      // the resolved labels for the ACTUAL producing
                      // port when it can (regroup echoes the input's
                      // dims). Fall back to the effective-static value
                      // when the backend has nothing to add.
                      dimLabels={
                        (target.dim_labels && target.dim_labels.length > 0
                          ? target.dim_labels
                          : effectivePortDimLabels(
                              port.arrayed,
                              port.dim_labels,
                              pack.arrayable,
                              arrayed_toggle,
                            ))
                      }
                    />
                  </>
                );
              }
              return (
                <>
                  {header}
                  <BasicInfoPreview
                    handleId={target.handle_id}
                    storage={target.storage}
                    absolutePath={target.absolute_path}
                  />
                </>
              );
            }
            // Partial preview: no aggregate target yet, but the arrayed
            // fanout has completed at least one shard for this port.
            // Route to Preview() with ``partial={…}`` so ArrayedPaginator
            // pages through the done shards while the parent job is
            // still running. Total is the parent's expected_shards
            // (via ``runtime.progress.total``) so the pager shows
            // ``i / expected`` instead of ``i / done_so_far``.
            const shardsForPort = previewShards?.[name] ?? [];
            const partialAvailable =
              expandKind === "viewer" && !target && shardsForPort.length > 0;
            if (partialAvailable) {
              const expectedTotal = runtime?.progress?.total ?? undefined;
              return (
                <>
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 6,
                      marginBottom: 6,
                      fontSize: 10,
                      color: "var(--inverse-muted)",
                    }}
                  >
                    <span style={{ flex: 1 }}>
                      {pack.name} · {name}
                    </span>
                    <span
                      title="fanout in progress — showing completed shards only"
                      style={{
                        padding: "1px 6px",
                        borderRadius: "var(--radius-pill)",
                        border: "1px solid var(--border-strong)",
                        fontSize: 9,
                        fontFamily: "var(--font-mono)",
                        color: "var(--text-muted)",
                      }}
                    >
                      partial
                    </span>
                  </div>
                  <Preview
                    spec={port.preview ?? undefined}
                    baseUrl=""
                    storage="dir"
                    tags={port.tags}
                    arrayed={effectivePortArrayed(
                      port.arrayed,
                      pack.arrayable,
                      arrayed_toggle,
                    )}
                    dimLabels={effectivePortDimLabels(
                      port.arrayed,
                      port.dim_labels,
                      pack.arrayable,
                      arrayed_toggle,
                    )}
                    partial={{
                      elements: shardsForPort.map((s) => ({
                        element_id: s.element_id,
                        proxy_url: s.proxy_url,
                      })),
                      expectedTotal,
                    }}
                  />
                </>
              );
            }
            // Placeholder for viewer-kind with no/deleted handle, and
            // info-kind with no handle yet (never ran).
            //
            // "Not ready" gating: don't flash a terminal verdict until we
            // have data that supports one. Two windows to cover:
            //
            //   * ``hydrating`` (from CanvasContext) — the App's initial
            //     cold-load chain (listWorkflowRuns → getSnapshot →
            //     getHandle batch) is still in flight. Persisted
            //     ``preview_open`` slots would otherwise flash "尚未运行"
            //     before jobs arrive, then "产物已被清理" between jobs
            //     landing (runState="done") and the handle batch
            //     resolving (target=null → treated as tombstoned).
            //   * ``runState === "done" && !target`` — post-hydration
            //     WS "done" frame arrived but the async getHandle
            //     hasn't resolved yet. Same "cleaned" flash on live
            //     runs; same fix.
            //
            // Once neither window applies, the terminal branch below
            // fires: ``target?.deleted`` → cleaned (real tombstone),
            // else → never-ran (no done job, no handle).
            const notReady = hydrating || (runState === "done" && !target);
            const placeholderKind = notReady
              ? "loading"
              : target?.deleted
                ? "cleaned"
                : "never-ran";
            return (
              <PreviewPlaceholder
                kind={placeholderKind}
                packName={pack.name}
                portName={name}
                running={runInFlight}
                readOnly={readOnly}
                onRun={
                  readOnly ? undefined : () => dispatchRunNode(id)
                }
              />
            );
          })()}
        </div>
      )}
    </div>
  );

  if (!arrayedOn) return card;

  // Arrayed nodes wear a thin outer frame with a top band labelling the
  // fan-out. Handles are pushed onto the frame's border via the
  // ``.hl-arrayed-frame`` CSS override in styles.css so the edges land
  // on the outermost visible edge rather than the inner card border.
  // xyflow measures the wrapper (frame + card), so TypedEdge's node-rect
  // avoidance automatically uses the enlarged bounds.
  const frameWidth = width + 2 * FRAME_PAD_SIDE + 2 * FRAME_BORDER;
  return (
    <div
      className="hl-arrayed-frame"
      data-hololab-node="algorithm-frame"
      style={{
        width: frameWidth,
        background: "var(--surface-alt)",
        border: `1px solid var(--border-subtle)`,
        borderRadius: "var(--radius-lg)",
        padding: `${FRAME_PAD_TOP}px ${FRAME_PAD_SIDE}px ${FRAME_PAD_SIDE}px`,
        position: "relative",
        boxShadow: "var(--rf-node-shadow)",
        fontFamily: "var(--font-sans)",
        color: "var(--text-body)",
        transition: "width 120ms ease-out",
      }}
    >
      <div
        data-hl-arrayed-band=""
        title="arrayed — pack fans out over its array input"
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          right: 0,
          height: FRAME_PAD_TOP,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: "var(--fs-micro)",
          color: "var(--text-muted)",
          fontFamily: "var(--font-mono)",
          letterSpacing: "0.14em",
          textTransform: "uppercase",
          fontWeight: "var(--fw-semibold)",
          lineHeight: 1,
          pointerEvents: "none",
          userSelect: "none",
        }}
      >
        arrayed
      </div>
      {card}
    </div>
  );
}

/** Play button placed at the far-right of the header — triggers a single-node
 *  dispatch. Slightly larger / more prominent than the icon-only utility buttons
 *  because "run" is the primary action on a node. */
function RunButton({
  pending,
  inFlight,
  stale,
  onClick,
}: {
  pending: boolean;
  inFlight: boolean;
  stale?: boolean;
  onClick: (e: React.MouseEvent) => void;
}) {
  const busy = pending || inFlight;
  // Amber-tinted border+background when this node is self-dirty so the
  // affordance for "click here to refresh outputs" is visible without
  // adding a separate button. Aligns with the staleness badge next to
  // the pack name so the same visual language reads across the header.
  const staleTint = Boolean(stale) && !busy;
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      title={
        inFlight
          ? "running…"
          : pending
            ? "dispatching…"
            : stale
              ? "run this node · 结果陈旧，点击从此节点重跑"
              : "run this node"
      }
      style={{
        border: `1px solid ${staleTint ? "var(--warning)" : "var(--border-strong)"}`,
        background: busy
          ? "var(--surface-3)"
          : staleTint
            ? "var(--warning-soft)"
            : "var(--surface-raised)",
        color: busy ? "var(--text-muted)" : staleTint ? "var(--warning)" : "var(--text)",
        width: 20,
        height: 20,
        borderRadius: "var(--radius-sm)",
        cursor: busy ? "default" : "pointer",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: 9,
        lineHeight: 1,
        padding: 0,
        flexShrink: 0,
        opacity: busy ? 0.5 : 1,
        transition:
          "opacity var(--dur-fast) var(--ease), background var(--dur-fast) var(--ease), border-color var(--dur-fast) var(--ease)",
      }}
    >
      {pending ? "…" : "▶"}
    </button>
  );
}

/** Footer's "what did the latest run do" summary. Left-aligned, flex-1
 *  so it consumes the space the compute-node label used to occupy. Four
 *  states drive four sentences:
 *
 *    * no runtime            → 未运行 (muted)
 *    * running               → 已跑 1m 26s (ticking, coloured by state)
 *    * pending / assigned    → the raw state word (waiting to start)
 *    * terminal (done/…/…)   → 1m 26s · 3 分钟前 (or coloured for failed)
 *
 *  All timestamps come from the aggregated NodeRuntime, which merges
 *  the newest generation's parent + shards (see canvas/nodeRuntime.ts).
 *  Rendered as a single span so overflow ellipsis works cleanly when the
 *  card is narrow. */
function FooterRunSummary({
  runtime,
  runColour,
  statusTitle,
}: {
  runtime: NodeRuntime | undefined;
  runColour: string;
  statusTitle: string;
}) {
  const state = runtime?.state;
  const inFlight = state ? IN_FLIGHT_STATES_FOR_TICK.has(state) : false;
  const now = useLiveNow(inFlight);

  const commonStyle: React.CSSProperties = {
    flex: 1,
    minWidth: 0,
    whiteSpace: "nowrap",
    overflow: "hidden",
    textOverflow: "ellipsis",
    fontVariantNumeric: "tabular-nums",
  };

  if (!runtime) {
    return (
      <span
        title="尚未运行"
        style={{ ...commonStyle, color: "var(--text-subtle)" }}
      >
        未运行
      </span>
    );
  }

  const startTs = runtime.started_ts ?? null;
  const updatedTs = runtime.updated_ts ?? null;

  if (state === "running") {
    const elapsed = startTs != null ? formatCardDuration(startTs, now) : "…";
    return (
      <span
        title={statusTitle}
        style={{
          ...commonStyle,
          color: runColour,
          fontWeight: "var(--fw-semibold)",
        }}
      >
        已跑 {elapsed}
      </span>
    );
  }

  if (state === "pending" || state === "assigned") {
    return (
      <span title={statusTitle} style={{ ...commonStyle, color: runColour }}>
        {state === "pending" ? "排队中" : "已派发…"}
      </span>
    );
  }

  // Terminal states — done / failed / cancelled / orphaned. Render
  // elapsed + relative time, coloured red only for failed so the eye
  // catches attention without turning every completed card into noise.
  const elapsed =
    startTs != null && updatedTs != null
      ? formatCardDuration(startTs, updatedTs)
      : null;
  const relative = updatedTs != null ? formatRelativeTime(updatedTs, now) : null;
  const isFailed = state === "failed";
  const parts: string[] = [];
  if (state === "failed") parts.push("失败");
  else if (state === "cancelled") parts.push("已取消");
  else if (state === "orphaned") parts.push("离线");
  if (elapsed) parts.push(elapsed);
  if (relative) parts.push(relative);
  const label = parts.length > 0 ? parts.join(" · ") : state ?? "—";

  return (
    <span
      title={statusTitle}
      style={{
        ...commonStyle,
        color: isFailed ? "var(--status-failed)" : "var(--text-muted)",
        fontWeight: isFailed ? "var(--fw-semibold)" : "var(--fw-regular)",
      }}
    >
      {label}
    </span>
  );
}

/** Small caret in the node header — flips on expand. Purely visual. */
/** Compact caret button — used in the footer bar to toggle the preview
 *  drawer. Sized to fit the 20 px footer without pushing it taller. */
function PreviewCaret({
  expanded,
  onClick,
}: {
  expanded: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      title={expanded ? "collapse preview" : "expand preview"}
      style={{
        border: "1px solid var(--border-strong)",
        background: expanded ? "var(--text)" : "transparent",
        color: expanded ? "var(--text-inverse)" : "var(--text-muted)",
        width: 16,
        height: 16,
        borderRadius: "var(--radius-sm)",
        cursor: "pointer",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: 9,
        lineHeight: 1,
        padding: 0,
        flexShrink: 0,
      }}
    >
      {expanded ? "▾" : "▸"}
    </button>
  );
}
