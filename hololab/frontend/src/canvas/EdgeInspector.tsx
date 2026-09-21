// Bottom-panel content shown when an edge is selected on the canvas.
//
// An edge in HoloLab represents *the data* flowing from a source
// output port to a target input port. This inspector answers three
// questions for the operator:
//
//   1. What type is on this wire? (source port's effective tag +
//      arrayed form — same string the edge chip shows on the canvas,
//      just laid out larger.)
//   2. Where does it flow? (source_node.port → target_node.port,
//      using each pack's human name.)
//   3. What's actually in it? (handle metadata + a preview; when the
//      edge's source has produced a run, we reuse the same tag → viewer
//      routing from previews.tsx that AlgorithmNode uses for its inline
//      drawer — no duplicate viewer logic.)
//
// The source handle id is what makes the "data" concrete. Draft view
// gets it from ``previewsByGraphNode`` (already resolved). Snapshot
// view gets it from the frozen ``SnapshotJob.output_handles``. Either
// way the inspector treats it as an opaque id + node id and fetches
// the current ``HandleInfo`` on mount so ``deleted_ts`` etc. are live.

import { useEffect, useState, type CSSProperties } from "react";
import { getHandle } from "../api";
import type {
  CatalogPack,
  ComputeNode,
  GraphNode,
  HandleInfo,
  OutputPortSpec,
} from "../wire";
import { effectivePortArrayed } from "../tags";
import { BasicInfoPreview, Preview } from "./previews";
import { OpenInCocoderButton } from "./OpenInCocoderButton";
import { CopyRefButton } from "./CopyRefButton";
import type { EdgeType } from "./edgeLabels";
import { formatTypeLabelLong } from "./edgeLabels";

export interface EdgeInspectorProps {
  /** Edge id — for display only (helps debugging cross-referencing). */
  edgeId: string;
  /** Effective type of the edge (tags + arrayed) — computed by the caller
   *  so it matches what the canvas chip shows exactly. */
  edgeType: EdgeType;
  /** Source-side context: the graph node + its pack + the port spec + the
   *  output handle id (null when no run has produced it yet). */
  source: {
    node: GraphNode;
    pack: CatalogPack | null;
    portName: string;
    portSpec: OutputPortSpec | null;
    handleId: string | null;
    /** Compute node that produced the handle. Null when handle isn't
     *  resolved yet — the Open-in-Cocoder button hides in that case. */
    computeNode: ComputeNode | null;
  };
  /** Target-side context — only names matter for the header. */
  target: {
    node: GraphNode;
    pack: CatalogPack | null;
    portName: string;
  };
}

const SECTION_TITLE: CSSProperties = {
  fontSize: 10,
  fontWeight: 600,
  letterSpacing: "0.08em",
  textTransform: "uppercase",
  color: "var(--text-subtle)",
  margin: "0 0 8px",
};

const TYPE_CHIP: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  padding: "2px 8px",
  borderRadius: "var(--radius-pill)",
  background: "var(--accent-soft)",
  color: "var(--accent)",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--fs-xs)",
  fontWeight: 600,
  letterSpacing: "0.01em",
  border: "1px solid var(--accent)",
};

const NODE_REF: CSSProperties = {
  display: "inline-flex",
  alignItems: "baseline",
  gap: 4,
  padding: "2px 6px",
  borderRadius: "var(--radius-sm)",
  background: "var(--surface-2)",
  border: "1px solid var(--border-subtle)",
  fontSize: "var(--fs-xs)",
  color: "var(--text-body)",
  minWidth: 0,
};

const PORT_NAME: CSSProperties = {
  fontFamily: "var(--font-mono)",
  color: "var(--text-muted)",
};

/** Compact "source → target" flow row. */
function FlowRow({
  source,
  target,
}: {
  source: EdgeInspectorProps["source"];
  target: EdgeInspectorProps["target"];
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        flexWrap: "wrap",
        marginBottom: 12,
        fontSize: "var(--fs-sm)",
      }}
    >
      <span style={NODE_REF} title={`${source.node.id}`}>
        <strong style={{ fontWeight: 600 }}>
          {source.pack?.name ?? source.node.algorithm_name}
        </strong>
        <span style={PORT_NAME}>· {source.portName}</span>
      </span>
      <span style={{ color: "var(--text-subtle)", fontSize: "var(--fs-sm)" }}>→</span>
      <span style={NODE_REF} title={`${target.node.id}`}>
        <strong style={{ fontWeight: 600 }}>
          {target.pack?.name ?? target.node.algorithm_name}
        </strong>
        <span style={PORT_NAME}>· {target.portName}</span>
      </span>
    </div>
  );
}

export function EdgeInspector({
  edgeId,
  edgeType,
  source,
  target,
}: EdgeInspectorProps) {
  const [handle, setHandle] = useState<
    | { kind: "idle" }
    | { kind: "loading" }
    | { kind: "ok"; info: HandleInfo }
    | { kind: "err"; message: string }
  >(() => (source.handleId ? { kind: "loading" } : { kind: "idle" }));

  useEffect(() => {
    if (!source.handleId) {
      setHandle({ kind: "idle" });
      return;
    }
    let cancelled = false;
    setHandle({ kind: "loading" });
    getHandle(source.handleId)
      .then((info) => {
        if (!cancelled) setHandle({ kind: "ok", info });
      })
      .catch((e: Error) => {
        if (!cancelled) setHandle({ kind: "err", message: e.message });
      });
    return () => {
      cancelled = true;
    };
  }, [source.handleId]);

  const info = handle.kind === "ok" ? handle.info : null;
  const showPreview =
    info && !info.deleted_ts && source.portSpec !== null;
  const effectiveArrayed = source.portSpec
    ? effectivePortArrayed(
        source.portSpec.arrayed,
        source.pack?.arrayable,
        Boolean(source.node.arrayed_toggle),
      )
    : edgeType.arrayed;

  return (
    <div
      style={{
        padding: "14px 20px 20px",
        height: "100%",
        overflowY: "auto",
        fontSize: "var(--fs-sm)",
        color: "var(--text-body)",
        minHeight: 0,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          marginBottom: 12,
          flexWrap: "wrap",
        }}
      >
        <span style={TYPE_CHIP} title={formatTypeLabelLong(edgeType)}>
          {formatTypeLabelLong(edgeType)}
        </span>
        <span
          style={{
            fontSize: 10,
            color: "var(--text-subtle)",
            letterSpacing: "0.06em",
            textTransform: "uppercase",
            fontWeight: 600,
          }}
        >
          Edge
        </span>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: "var(--fs-micro)",
            color: "var(--text-subtle)",
          }}
          title={edgeId}
        >
          {edgeId.slice(0, 10)}
        </span>
      </div>

      <h3 style={SECTION_TITLE}>Flow</h3>
      <FlowRow source={source} target={target} />

      <h3 style={SECTION_TITLE}>Data</h3>

      {handle.kind === "idle" && (
        <div
          style={{
            padding: "10px 12px",
            border: "1px dashed var(--border)",
            borderRadius: "var(--radius-sm)",
            color: "var(--text-muted)",
            fontSize: "var(--fs-xs)",
            lineHeight: 1.5,
          }}
        >
          No artifact produced on this wire yet. Run the source node
          (<code style={{ fontFamily: "var(--font-mono)" }}>{source.pack?.name ?? source.node.algorithm_name}</code>)
          to materialise the data.
        </div>
      )}
      {handle.kind === "loading" && (
        <div style={{ color: "var(--text-muted)", fontSize: "var(--fs-xs)" }}>
          loading handle…
        </div>
      )}
      {handle.kind === "err" && (
        <div style={{ color: "var(--error)", fontSize: "var(--fs-xs)" }}>
          handle lookup failed: {handle.message}
        </div>
      )}

      {info && (
        <>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              marginBottom: 8,
              fontSize: 10,
              color: "var(--inverse-muted)",
              padding: "6px 8px",
              background: "var(--inverse-surface)",
              borderRadius: "var(--radius-sm) var(--radius-sm) 0 0",
            }}
          >
            <span style={{ flex: 1, fontFamily: "var(--font-mono)" }}>
              {source.pack?.name ?? source.node.algorithm_name} · {source.portName}
              {info.deleted_ts !== null && (
                <span style={{ marginLeft: 8, color: "var(--warning)" }}>
                  · cleaned
                </span>
              )}
            </span>
            <OpenInCocoderButton
              path={info.absolute_path}
              computeNode={source.computeNode}
              onDark
            />
            <CopyRefButton
              kind="handle"
              id={info.handle_id}
              comment={`${source.pack?.name ?? source.node.algorithm_name} · ${source.portName} output`}
              size="xs"
              onDark
            />
          </div>
          <div style={{ marginTop: -8 }}>
            {showPreview && source.portSpec?.preview ? (
              <Preview
                spec={source.portSpec.preview}
                baseUrl={info.proxy_url}
                storage={info.storage}
                handleId={info.handle_id}
                absolutePath={info.absolute_path}
                producingNode={source.computeNode}
                // Prefer the runtime handle's tag list (resolved through
                // ``tags_from`` at handle_register) over the manifest's
                // static declaration — a generic ``regroup.out`` handle
                // fed by an ``image`` source arrives here as ``["image"]``
                // and picks the right viewer without a pack-level change.
                // See AlgorithmNode.tsx for the identical fallback.
                tags={info.tags.length > 0 ? info.tags : source.portSpec.tags}
                arrayed={effectiveArrayed}
              />
            ) : showPreview && (
                info.tags.includes("image") ||
                info.tags.includes("image_sequence") ||
                info.tags.includes("frame_sequence") ||
                source.portSpec?.tags.includes("frame_sequence")
              ) ? (
              // Tag-routed viewers (frame_sequence family) don't need
              // a preview spec — Preview dispatches on tags first.
              // Passing a dummy spec keeps the signature simple; the
              // spec is unused for this branch. Runtime handle tags
              // are checked first so a generic ``tags_from`` port
              // (e.g. ``regroup.out``) whose static declaration is
              // ``[any]`` still routes correctly once the aggregate
              // lands with resolved ``["image"]``.
              <Preview
                spec={{ viewer: "image", member: null }}
                baseUrl={info.proxy_url}
                storage={info.storage}
                handleId={info.handle_id}
                absolutePath={info.absolute_path}
                producingNode={source.computeNode}
                tags={info.tags.length > 0 ? info.tags : source.portSpec?.tags}
                arrayed={effectiveArrayed}
              />
            ) : (
              <BasicInfoPreview
                handleId={info.handle_id}
                storage={info.storage}
                absolutePath={info.absolute_path}
                fallbackSize={info.size_bytes}
              />
            )}
          </div>
        </>
      )}
    </div>
  );
}
