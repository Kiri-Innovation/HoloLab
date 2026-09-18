// Custom xyflow node for one algorithm-pack instance.
//
// The node's shape is manifest-driven: inputs on the left, outputs on the
// right, each rendered as a coloured dot that carries the port's tags via
// dataset attributes. The App-level onConnect validator reads those tags to
// enforce tag compatibility at edge-drawing time.

import { useMemo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import type {
  CatalogPack,
  ComputeNode,
  InputPortSpec,
  OutputPortSpec,
  PortSpec,
} from "../wire";
import { effectivePortArrayed, firstTagColour } from "../tags";
import { CopyRefButton } from "./CopyRefButton";
import { OpenInCocoderButton } from "./OpenInCocoderButton";
import { OpenSourceButton } from "./OpenSourceButton";
import { Preview } from "./previews";
import { PreviewPlaceholder } from "./PreviewPlaceholder";

// The port label shown on the canvas is the port's tag (its object type).
// The internal port variable name (which the pack's shell template uses) is
// hidden in the tooltip — graph authors care about the type, not the shell.
function portLabel(tags: string[]): string {
  return tags[0] ?? "?";
}

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
  // The parent workflow's id. Threaded through so the card-header ⧉
  // can emit a ``hololab://graph-node/<workflow_id>/<graph_node_id>``
  // reference (graph node id alone is only unique inside its workflow).
  // Snapshot canvas passes its snapshot's workflow_id; draft canvas
  // passes the currently-open workflow_id.
  workflow_id?: string | null;
  runtime?: NodeRuntime;
  // Resolved preview URLs for each output port that has a preview
  // declaration AND has produced a handle. Key is the output port name.
  previews?: Record<string, PreviewTarget>;
  // Frontend-only: which preview drawer is expanded, if any. The node
  // grows a slot below its body when set. ``null`` = collapsed.
  previewOpen?: string | null;
  // Compute-node map keyed by ``node_id``. The preview drawer's
  // "Open in Cocoder" button looks up the producing node here to
  // resolve ``flops_executor_id`` at render time — that way a
  // NodeSettingsDrawer edit is visible in the drawer immediately,
  // without a workflow autosave round-trip.
  computeNodesById?: Record<string, ComputeNode>;
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

const ROW: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
  fontSize: "var(--fs-xs)",
  fontFamily: "var(--font-mono)",
  padding: "var(--space-1) 0",
};

const NODE_WIDTH = 220;
const NODE_WIDTH_EXPANDED = 340; // wider slot so previews have room to breathe

// A previewable output = an output port that has a preview declaration
// in the manifest. The caret shows whenever the pack has ANY such port,
// even if no run has resolved a handle yet — clicking still opens the
// drawer, which renders a "never-ran" placeholder + Run button so the
// operator can trigger the first run in place.
//
// The optional ``target`` field carries the resolved handle when the
// job has produced one; when absent (or when target.deleted is true)
// the drawer swaps in PreviewPlaceholder.
function previewableOutputs(
  pack: CatalogPack,
  previews: Record<string, PreviewTarget> | undefined,
): Array<{ name: string; spec: OutputPortSpec; target: PreviewTarget | null }> {
  const out: Array<{
    name: string;
    spec: OutputPortSpec;
    target: PreviewTarget | null;
  }> = [];
  for (const [name, spec] of Object.entries(pack.outputs)) {
    if (!spec.preview) continue;
    out.push({ name, spec, target: previews?.[name] ?? null });
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
    workflow_id: workflowId,
    runtime,
    previews,
    previewOpen,
    onPreviewToggle,
    computeNodesById,
    readOnly,
    arrayed_toggle,
  } = data as AlgorithmNodeData & { arrayed_toggle?: boolean };
  const arrayedOn = Boolean(arrayed_toggle && pack.arrayable);
  const inputEntries = Object.entries(pack.inputs);
  const outputEntries = Object.entries(pack.outputs);
  const rows = Math.max(inputEntries.length, outputEntries.length);
  const runState = runtime?.state;
  const runColour = stateColour(runState);
  const previewables = previewableOutputs(pack, previews);
  // Expand a port so long as it is previewable (has a preview spec on
  // the pack). The drawer decides at render time whether to show the
  // real Preview, the never-ran placeholder, or the cleaned placeholder.
  const previewableNames = useMemo(
    () => new Set(previewables.map((p) => p.name)),
    [previewables],
  );
  const expanded =
    previewOpen && previewableNames.has(previewOpen) ? previewOpen : null;
  const width = expanded ? NODE_WIDTH_EXPANDED : NODE_WIDTH;
  const currentPreview =
    expanded ? previewables.find((p) => p.name === expanded) ?? null : null;
  const runInFlight =
    runState === "pending" || runState === "assigned" || runState === "running";

  return (
    <div
      className="hololab-node"
      style={{
        width,
        background: "var(--surface-2)",
        border: `1px solid ${selected ? "var(--accent)" : "var(--border-strong)"}`,
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
      {/* header */}
      <div
        style={{
          padding: "var(--space-2) var(--space-3)",
          borderBottom: "1px solid var(--border)",
          background: "var(--surface-raised)",
          borderRadius: "var(--radius-md) var(--radius-md) 0 0",
          display: "flex",
          alignItems: "center",
          gap: 6,
        }}
      >
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontWeight: 700,
              fontSize: "var(--fs-lg)",
              color: "var(--text)",
              lineHeight: "var(--lh-tight)",
              letterSpacing: "-0.005em",
            }}
          >
            {pack.name}
          </div>
          <div
            style={{
              fontSize: "var(--fs-micro)",
              color: "var(--text-muted)",
              fontFamily: "var(--font-mono)",
              marginTop: 1,
            }}
          >
            v{pack.version}
            {assigned_node_id ? ` · @ ${assigned_node_id.slice(0, 6)}` : " · not assigned"}
          </div>
        </div>
        {arrayedOn && (
          <div
            data-hl-arrayed-badge=""
            title="并行处理数组输入 — 运行时框架为每个数组元素起一个 sub-job"
            style={{
              background: "var(--warning-soft, rgba(200, 162, 0, 0.14))",
              color: "var(--warning, #c8a200)",
              border: "1px solid var(--warning, #c8a200)",
              fontSize: "var(--fs-micro)",
              fontWeight: 700,
              padding: "1px 5px",
              borderRadius: "var(--radius-sm, 4px)",
              lineHeight: "14px",
              letterSpacing: "0.04em",
              whiteSpace: "nowrap",
            }}
          >
            [N]
          </div>
        )}
        {runState && (
          <div
            style={{
              background: "var(--surface-alt)",
              color: runColour,
              border: "1px solid var(--border)",
              fontSize: "var(--fs-micro)",
              fontWeight: 600,
              padding: "1px 6px",
              borderRadius: "var(--radius-pill)",
              lineHeight: "14px",
              letterSpacing: "0.02em",
              whiteSpace: "nowrap",
            }}
            title={
              runtime?.fail_reason
                ? `${runState} · ${runtime.fail_reason}`
                : runState
            }
          >
            {runState}
            {runtime?.progress && runtime.progress.total > 0
              ? ` ${runtime.progress.current}/${runtime.progress.total}`
              : ""}
          </div>
        )}
        {/*
          The card-header ⧉ copies the GRAPH-NODE ref (position on the
          canvas), not the current job id. Resolving a graph-node ref
          returns the latest job / snapshot / handles at this position
          plus a ready dispatch URL, so an agent still reaches the same
          places in one hop — but the ref itself is stable across every
          Fork, and remains valid even before the first run.

          For per-execution grabs (a specific job the user wants to
          point at) the RecentJobs panel row keeps its own ⧉ that
          emits kind="job".

          Rendered only when workflow_id is available — without it we
          can't form a valid ref (graph_node_id is only unique inside
          its workflow). In practice the palette (an unpersisted
          workflow being composed) is the only path that omits it.
        */}
        {/*
          Code-icon button: opens the pack's source in Cocoder via
          window.flops.showDocument. Renders only inside Flops
          (flopsAvailable()); resolves the target compute node in
          this priority order:
            1. The graph-node's explicit assignment (assigned_node_id),
               so the developer picks the specific machine when there
               are multiple online offering the same pack.
            2. First entry of pack.node_ids (currently-online nodes).
            3. null — button still renders (guide the operator to
               configure things).
        */}
        <OpenSourceButton
          pack={pack}
          computeNode={
            (assigned_node_id && computeNodesById?.[assigned_node_id]) ||
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
        {previewables.length > 0 && (
          <PreviewCaret
            expanded={Boolean(expanded)}
            onClick={() => {
              const next = expanded ? null : previewables[0].name;
              if (onPreviewToggle) {
                onPreviewToggle(next);
              } else {
                dispatchToggle(id, next);
              }
            }}
          />
        )}
      </div>

      {/* body: two aligned columns of inputs / outputs */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          padding: "var(--space-2) var(--space-3) var(--space-3)",
          minHeight: 24,
          rowGap: 4,
        }}
      >
        {Array.from({ length: rows }).map((_, i) => {
          const inp = inputEntries[i];
          const out = outputEntries[i];
          return (
            <div key={`row-${i}`} style={{ display: "contents" }}>
              {/* input side */}
              <div style={{ ...ROW, position: "relative" }}>
                {inp && (
                  <>
                    <Handle
                      type="target"
                      position={Position.Left}
                      id={inp[0]}
                      style={{
                        ...DOT,
                        background: firstTagColour(inp[1].tags),
                        ...(effectivePortArrayed(
                          inp[1].arrayed,
                          pack.arrayable,
                          arrayed_toggle,
                        )
                          ? ARRAYED_DOT_OVERLAY
                          : {}),
                      }}
                      data-tags={inp[1].tags.join(",")}
                      data-required={inp[1].required ? "1" : "0"}
                      data-hl-arrayed-port={
                        effectivePortArrayed(
                          inp[1].arrayed,
                          pack.arrayable,
                          arrayed_toggle,
                        )
                          ? ""
                          : undefined
                      }
                    />
                    <span
                      style={{
                        marginLeft: 10,
                        color: inp[1].required ? "var(--text-body)" : "var(--text-subtle)",
                      }}
                      title={inputTitle(inp[0], inp[1])}
                    >
                      {portLabel(inp[1].tags)}
                      {!inp[1].required && "?"}
                    </span>
                  </>
                )}
              </div>
              {/* output side */}
              <div
                style={{
                  ...ROW,
                  position: "relative",
                  justifyContent: "flex-end",
                }}
              >
                {out && (
                  <>
                    <span
                      style={{ marginRight: 10, color: "var(--text-body)" }}
                      title={outputTitle(out[0], out[1])}
                    >
                      {portLabel(out[1].tags)}
                    </span>
                    <Handle
                      type="source"
                      position={Position.Right}
                      id={out[0]}
                      style={{
                        ...DOT,
                        background: firstTagColour(out[1].tags),
                        ...(effectivePortArrayed(
                          out[1].arrayed,
                          pack.arrayable,
                          arrayed_toggle,
                        )
                          ? ARRAYED_DOT_OVERLAY
                          : {}),
                      }}
                      data-tags={out[1].tags.join(",")}
                      data-hl-arrayed-port={
                        effectivePortArrayed(
                          out[1].arrayed,
                          pack.arrayable,
                          arrayed_toggle,
                        )
                          ? ""
                          : undefined
                      }
                    />
                  </>
                )}
              </div>
            </div>
          );
        })}
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
            const { name, spec: port, target } = currentPreview;
            // Route: live preview when we have a non-deleted target;
            // otherwise the placeholder tells the operator why (never
            // ran vs cleaned) and offers Run this node.
            const showRealPreview = target !== null && !target.deleted;
            if (showRealPreview && target && port.preview) {
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
                  />
                </>
              );
            }
            // Placeholder classification: a tombstoned handle is
            // definitely "cleaned"; otherwise if the last job reached
            // done but no live target exists the handle was cleaned
            // externally / before we could load it → also "cleaned";
            // else the node genuinely has no run yet.
            const kind =
              target?.deleted || runState === "done" ? "cleaned" : "never-ran";
            return (
              <PreviewPlaceholder
                kind={kind}
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

/** Small caret in the node header — flips on expand. Purely visual. */
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
        background: expanded ? "var(--text)" : "var(--surface)",
        color: expanded ? "var(--text-inverse)" : "var(--text-body)",
        width: "var(--control-h-sm)",
        height: "var(--control-h-sm)",
        borderRadius: "var(--radius-sm)",
        cursor: "pointer",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: "var(--fs-micro)",
        lineHeight: 1,
        padding: 0,
      }}
    >
      {expanded ? "▾" : "▸"}
    </button>
  );
}
