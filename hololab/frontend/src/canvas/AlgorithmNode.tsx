import { canonicalPackageName } from "../packageIdentity";
import { packCertification } from "./packCertification";
import { ImageCropEditor } from "./ImageCropEditor";
import { ComposeVideoEditor } from "./ComposeVideoEditor";
import { InlinePortCard, activeInlineValue } from "./CanvasInlineValues";
import { inputValueProblems } from "./inlineValues";
import { PinnedParameters } from "./PinnedParameters";
import { NodePreviews, useNodePreviewSelection } from "./NodePreviews";
import { PortHandle } from "./PortHandle";
// Custom xyflow node for one algorithm-pack instance.
//
// The node's shape is manifest-driven: inputs on the left, outputs on the
// right, each rendered as a coloured dot that carries the port's tags via
// dataset attributes. The App-level onConnect validator reads those tags to
// enforce tag compatibility at edge-drawing time.

import { useEffect, useState } from "react";
import { NodeRunControl } from "./NodeRunControl";
import { nodeStatus } from "./nodeStatus";
import { NodeCardHeader } from "./NodeCardHeader";
import { Position, useStore, type NodeProps } from "@xyflow/react";
import type {
  CatalogPack,
  OutputPreviewSpec,
} from "../wire";
import { effectivePortArrayed, effectivePortDimLabels, firstTagColour } from "../tags";
import { useCanvasContext } from "./CanvasContext";
import type { NodeStaleness } from "./staleness";
import { OpenArtifactLocationButton } from "./OpenArtifactLocationButton";
import type { LiveViewerFrame } from "./NodeViewerFrame";

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
  /** Run whose job association supplied this preview (not necessarily the newest run). */
  snapshot_id?: string | null;
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
  // Exact rectangular per-dim element counts, outer-first. Null for scalar /
  // file-storage or ragged trees; runtime_shape retains partial/range evidence.
  dim_sizes: number[] | null;
}

export interface AlgorithmNodeData extends Record<string, unknown> {
  assembly?: import("./assembly").AssemblyConfig;
  pack: CatalogPack;
  arrayed_toggle?: boolean;
  assigned_node_id: string | null;
  /** Draft node parameters, included in the CoBrowser reference snapshot. */
  local_params?: string[];
  param_bindings?: Record<string, string>;
  params?: Record<string, unknown>;
  port_values?: Record<string, unknown>;
  runtime?: NodeRuntime;
  // Resolved preview URLs for each output port that has a preview
  // declaration AND has produced a handle. Key is the output port name.
  previews?: Record<string, PreviewTarget>;
  // Per-port list of shard previews, for arrayed nodes mid-fanout.
  // Populated as each shard completes; consumed by the drawer when the
  // aggregate target isn't in ``previews`` yet.
  previewShards?: Record<string, PreviewShard[]>;
  // Non-terminal files announced by the node runtime for pack-owned viewers.
  nodeViewerFrames?: Record<string, LiveViewerFrame[]>;
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
  /** Snapshot-frozen pins; draft pins come from workflow context. */
  pinnedParams?: string[];
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

export function dispatchRunNode(graph_node_id: string): Promise<void> {
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
    onPreviewToggle,
    readOnly,
    staleness,
    arrayed_toggle,
  } = data as AlgorithmNodeData;
  const {
    computeNodesById,
    packDisplayName,
  } = useCanvasContext();
  const arrayedOn = Boolean(arrayed_toggle && pack.arrayable);
  const inputEntries = Object.entries(pack.inputs);
  const outputEntries = Object.entries(pack.outputs);
  const connectedInputs = useStore(s => JSON.stringify(s.edges.filter(e => e.target === id).map(e => e.targetHandle)));
  const wiredInputs: (string | null)[] = JSON.parse(connectedInputs);
  const missingInputs = inputValueProblems(pack.inputs, (data as AlgorithmNodeData).port_values ?? {}, wiredInputs, arrayedOn);
  const inputProblem = missingInputs.length ? `输入未就绪：${missingInputs.join("、")}。运行会被拒绝，请检查输入来源和值。` : undefined;
  const runState = runtime?.state;
  const runColour = stateColour(runState);
  const previewSelection = useNodePreviewSelection(data as AlgorithmNodeData);
  const { defaultNodeViewer, expandables, currentPreview } = previewSelection;
  const expanded = currentPreview?.name ?? null;
  const width =
    (defaultNodeViewer || expanded)
      ? NODE_WIDTH_EXPANDED
      : NODE_WIDTH;
  const runInFlight =
    runState === "pending" || runState === "assigned" || runState === "running";

  const [showCompositionOutput, setShowCompositionOutput] = useState(false);
  const [runClickPending, setRunClickPending] = useState(false);
  const [runErrorMsg, setRunErrorMsg] = useState<string | null>(null);

  useEffect(() => { if (!inputProblem) setRunErrorMsg(null); }, [inputProblem]);

  const handleRunClick = async () => {
    if (runInFlight || runClickPending) return;
    if (inputProblem) { setRunErrorMsg(inputProblem); return; }
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
  const certification = packCertification(pack);

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
      <NodeCardHeader id={id} name={pack.name} displayName={packDisplayName?.(pack.name) ?? pack.name} version={pack.version} device={assignedLabel}
        certification={certification}
        subtitle={`${assignedLabel} · v${pack.version}${pack.version.endsWith("+") ? " · dev" : ""}`}
        state={runState} status={nodeStatus({ state: runState, stale: staleness, failReason: runtime?.fail_reason, missingAlgorithm: pack.manifest_hash ? null : {name: pack.name, version: pack.version}, missingInputs, readOnly })}
        actions={!readOnly && <NodeRunControl id={id} pending={runClickPending} state={runState} jobId={runtime?.job_id}
          stale={staleness?.kind === "self_dirty"} blockedReason={inputProblem} onRun={handleRunClick} />}>
        {runErrorMsg && !inputProblem && (
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
      </NodeCardHeader>

      {inputProblem && <button type="button" className="nodrag" data-hl-missing-inputs=""
        title={inputProblem} onClick={e => { e.stopPropagation(); setRunErrorMsg(inputProblem); }}
        style={{display: "block", width: "100%", height: "auto", whiteSpace: "normal", lineHeight: 1.5, border: 0, background: "transparent", color: "var(--status-failed)", padding: "6px 12px", fontSize: "var(--fs-xs)", textAlign: "left", overflowWrap: "anywhere"}}>
        输入未就绪：{missingInputs.join("、")}
        {runErrorMsg === inputProblem && <span style={{display: "block", marginTop: 4}}>运行会被拒绝，请检查输入来源和值。</span>}
      </button>}
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
              spec.scalar,
            );
            const inline = activeInlineValue(spec, (data as AlgorithmNodeData).port_values, portName, wiredInputs.includes(portName), readOnly);
            return (
              <div key={`in-${portName}`} style={{ ...PORT_ROW, position: "relative", ...(inline != null ? {minHeight: 42} : {}) }}>
                {inline != null && <InlinePortCard id={id} name={portName} spec={spec} value={inline}
                  array={!!(isArrayed || spec.dim_labels?.length)} frozen={readOnly} />}
                <PortHandle
                  nodeId={id}
                  declared={{ tags: spec.tags, arrayed: isArrayed, dimLabels: effectivePortDimLabels(spec.arrayed, spec.dim_labels, pack.arrayable, arrayed_toggle, spec.scalar) }}
                  type="target"
                  issue={missingInputs.includes(portName) ? "输入缺失或值格式无效。运行会被拒绝，请检查输入来源和值。" : undefined}
                  position={Position.Left}
                  id={portName}
                  style={{
                    ...DOT,
                    background: !wiredInputs.includes(portName) ? "var(--surface-2)" : firstTagColour(spec.tags),
                    ...(isArrayed ? ARRAYED_DOT_OVERLAY : {}),
                    ...(missingInputs.includes(portName) ? {border: "2px solid var(--status-failed)", boxShadow: "0 0 0 2px var(--surface-2), 0 0 0 3px var(--status-failed)"} : {}),
                  }}
                  data-tags={spec.tags.join(",")}
                  data-required={spec.required ? "1" : "0"}
                  data-hl-arrayed-port={isArrayed ? "" : undefined}
                />
                <PortHandle asText nodeId={id} id={portName} type="target" position={Position.Left}
                  issue={missingInputs.includes(portName) ? "输入缺失或值格式无效。运行会被拒绝，请检查输入来源和值。" : undefined}
                  handleId={undefined}
                  declared={{ tags: spec.tags, arrayed: isArrayed, dimLabels: effectivePortDimLabels(spec.arrayed, spec.dim_labels, pack.arrayable, arrayed_toggle, spec.scalar) }}
                  style={{
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
                </PortHandle>
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
                <PortHandle asText nodeId={id} id={portName} type="source" position={Position.Right}
                  handleId={previews?.[portName]?.handle_id}
                  declared={{ tags: spec.tags, arrayed: isArrayed, dimLabels: effectivePortDimLabels(spec.arrayed, spec.dim_labels, pack.arrayable, arrayed_toggle) }}
                  style={{
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
                </PortHandle>
                <PortHandle
                  nodeId={id}
                  declared={{ tags: spec.tags, arrayed: isArrayed, dimLabels: effectivePortDimLabels(spec.arrayed, spec.dim_labels, pack.arrayable, arrayed_toggle) }}
                  type="source"
                  handleId={previews?.[portName]?.handle_id}
                  position={Position.Right}
                  id={portName}
                  style={{
                    ...DOT,
                    background: inputProblem ? "var(--text-subtle)" : firstTagColour(spec.tags),
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

      <PinnedParameters id={id} data={data as AlgorithmNodeData} />

      {/* FOOTER — run result: elapsed + relative-time + progress + actions.
          Header carries identity (compute-node · version); footer is
          reserved for the latest run's outcome. */}
      <div
        data-hl-node-footer=""
        style={{
          padding: "var(--space-1)",
          paddingLeft: "var(--space-3)",
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
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-1)", flex: 1, minWidth: 0 }}>
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
                flexShrink: 0,
              }}
              data-tooltip={statusTitle}
            >
              {progressLabel}
            </span>
          )}
        </div>
        {/* Separate simultaneous focus/hover fills by half a spacing step (2px); retain 4px right inset and 8px to metadata. */}
        <div style={{ display: "flex", alignItems: "center", gap: "calc(var(--space-1) / 2)", flexShrink: 0 }}>
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
            <button
              type="button"
              className="hl-icon-button hl-button--ghost hl-node-footer-action"
              aria-expanded={Boolean(expanded)}
              aria-label={expanded ? "收起预览" : "展开预览"}
              data-tooltip={expanded ? "收起预览" : "展开预览"}
              onClick={(e) => {
                e.stopPropagation();
                const next = expanded ? null : expandables[0].name;
                if (onPreviewToggle) onPreviewToggle(next); else dispatchToggle(id, next);
              }}
              style={{ flexShrink: 0 }}
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
                stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"
                aria-hidden="true" focusable="false">
                <path d={expanded ? "m6 15 6-6 6 6" : "m6 9 6 6 6-6"} />
              </svg>
            </button>
          )}
        </div>
      </div>

      {inputProblem && firstArtifactTarget && <div data-hl-historical-preview="" style={{padding: "6px 12px", fontSize: "var(--fs-xs)", color: "var(--text-muted)", overflowWrap: "anywhere"}}>
        历史运行产物 · 不代表当前配置可运行
      </div>}
      {canonicalPackageName(pack.name) === "kiri/compose-video" && expanded ? <>
        <ComposeVideoEditor id={id} readOnly={readOnly} />
        <details className="nodrag" onToggle={e=>setShowCompositionOutput(e.currentTarget.open)} style={{padding:8,fontSize:"var(--fs-xs)"}}><summary>上次运行产物</summary>
          {showCompositionOutput && <NodePreviews data={data as AlgorithmNodeData} selection={previewSelection} onRun={inputProblem ? undefined : () => dispatchRunNode(id)} />}
        </details>
      </> : canonicalPackageName(pack.name) === "kiri/image-crop" && expanded ? <>
        <ImageCropEditor id={id} readOnly={readOnly} />
        <details className="nodrag" style={{padding:8,fontSize:"var(--fs-xs)"}}><summary>上次运行产物</summary>
          <NodePreviews data={data as AlgorithmNodeData} selection={previewSelection} onRun={inputProblem ? undefined : () => dispatchRunNode(id)} />
        </details>
      </> : <NodePreviews data={data as AlgorithmNodeData} selection={previewSelection} onRun={inputProblem ? undefined : () => dispatchRunNode(id)} />}
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
        data-tooltip="尚未运行"
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
        data-tooltip={statusTitle}
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
      <span data-tooltip={statusTitle} style={{ ...commonStyle, color: runColour }}>
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
      data-tooltip={`${label} · ${statusTitle}`}
      tabIndex={0}
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
