// Custom xyflow node for one algorithm-pack instance.
//
// The node's shape is manifest-driven: inputs on the left, outputs on the
// right, each rendered as a coloured dot that carries the port's tags via
// dataset attributes. The App-level onConnect validator reads those tags to
// enforce tag compatibility at edge-drawing time.

import { useMemo, useState } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import type {
  CatalogPack,
  InputPortSpec,
  OutputPortSpec,
  PortSpec,
} from "../wire";
import { effectivePortArrayed, firstTagColour } from "../tags";
import { useCanvasContext } from "./CanvasContext";
import { CopyRefButton } from "./CopyRefButton";
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
}

export interface AlgorithmNodeData extends Record<string, unknown> {
  pack: CatalogPack;
  assigned_node_id: string | null;
  runtime?: NodeRuntime;
  // Resolved preview URLs for each output port that has a preview
  // declaration AND has produced a handle. Key is the output port name.
  previews?: Record<string, PreviewTarget>;
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

function expandableOutputs(
  pack: CatalogPack,
  previews: Record<string, PreviewTarget> | undefined,
): ExpandablePort[] {
  const out: ExpandablePort[] = [];
  for (const [name, spec] of Object.entries(pack.outputs)) {
    const target = previews?.[name] ?? null;
    if (spec.preview) {
      out.push({ name, spec, target, kind: "viewer" });
    } else if (target) {
      out.push({ name, spec, target, kind: "info" });
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
}

function dispatchRunNode(graph_node_id: string): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    window.dispatchEvent(
      new CustomEvent<RunNodeDetail>(RUN_NODE_EVENT, {
        detail: {
          graph_node_id,
          resolve,
          reject: (msg) => reject(new Error(msg)),
        },
      }),
    );
  });
}

export function AlgorithmNode({ id, data, selected }: NodeProps) {
  const {
    pack,
    assigned_node_id,
    runtime,
    previews,
    previewOpen,
    onPreviewToggle,
    readOnly,
    arrayed_toggle,
  } = data as AlgorithmNodeData & { arrayed_toggle?: boolean };
  const { workflow_id: workflowId, computeNodesById } = useCanvasContext();
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
  const width = expanded ? NODE_WIDTH_EXPANDED : NODE_WIDTH;
  const currentPreview =
    expanded ? expandables.find((p) => p.name === expanded) ?? null : null;
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
      setTimeout(() => setRunErrorMsg(null), 4000);
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

  return (
    <div
      className="hololab-node"
      style={{
        width,
        background: "var(--surface-2)",
        border: `1px solid ${selected ? "var(--accent)" : failedTint ? "var(--status-failed)" : "var(--border-strong)"}`,
        borderRadius: "var(--radius-md)",
        boxShadow: "var(--rf-node-shadow)",
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
        <div
          title={`${pack.name} v${pack.version}`}
          style={{
            flex: 1,
            minWidth: 0,
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
        </div>
        {arrayedOn && (
          <span
            title="并行化 — 框架为每个数组元素起一个 sub-job"
            style={{
              flex: "0 0 auto",
              fontSize: "var(--fs-xs)",
              color: "var(--text-muted)",
              fontFamily: "var(--font-sans)",
              fontWeight: "var(--fw-normal)",
              letterSpacing: 0,
              whiteSpace: "nowrap",
            }}
          >
            (arrayed)
          </span>
        )}
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

      {/* FOOTER — compute-node · version · arrayed chip · expand caret */}
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
        <span
          title={assigned_node_id ?? "no compute node assigned"}
          style={{
            flex: 1,
            minWidth: 0,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
            color: assigned_node_id ? "var(--text-muted)" : "var(--text-subtle)",
          }}
        >
          {assignedLabel} · v{pack.version}
        </span>
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
              if (expandKind === "viewer" && port.preview) {
                return (
                  <>
                    {header}
                    <Preview
                      spec={port.preview}
                      baseUrl={target.proxy_url}
                      storage={target.storage}
                      handleId={target.handle_id}
                      absolutePath={target.absolute_path}
                      producingNode={
                        computeNodesById?.[target.node_id] ?? null
                      }
                      tags={port.tags}
                      arrayed={effectivePortArrayed(
                        port.arrayed,
                        pack.arrayable,
                        arrayed_toggle,
                      )}
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
            // Only viewer-kind ports fall back to the never-ran /
            // cleaned placeholder — info-kind ports only appear in
            // the expandables list when a target already exists, so
            // this branch is unreachable for them (guarded by
            // expandableOutputs above; kept exhaustive for clarity).
            const placeholderKind =
              target?.deleted || runState === "done" ? "cleaned" : "never-ran";
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
}

/** Play button placed at the far-right of the header — triggers a single-node
 *  dispatch. Slightly larger / more prominent than the icon-only utility buttons
 *  because "run" is the primary action on a node. */
function RunButton({
  pending,
  inFlight,
  onClick,
}: {
  pending: boolean;
  inFlight: boolean;
  onClick: (e: React.MouseEvent) => void;
}) {
  const busy = pending || inFlight;
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
            : "run this node"
      }
      style={{
        border: "1px solid var(--border-strong)",
        background: busy ? "var(--surface-3)" : "var(--surface-raised)",
        color: busy ? "var(--text-muted)" : "var(--text)",
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
        transition: "opacity var(--dur-fast) var(--ease)",
      }}
    >
      {pending ? "…" : "▶"}
    </button>
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
