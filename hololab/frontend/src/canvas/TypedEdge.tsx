// Custom xyflow edge that renders a small "data type" chip near the
// midpoint of the wire and highlights when selected.
//
// Design:
//   * The chip carries the source port's effective type (tag + arrayed
//     form — same vocabulary as the port dot's ``data-tags`` and the
//     inspector). The label string is computed by the enclosing canvas
//     and stashed on ``edge.data`` so this component stays a pure
//     renderer (no catalog lookups here).
//   * Default weight is deliberately quiet — a low-contrast chip a
//     touch smaller than a port label. Selection and hover both bump
//     opacity/border so the chip pops without ever getting loud.
//   * Selection restyles the wire itself (stroke, colour) so a click
//     communicates the edge is the current subject — the pattern
//     matches the node's blue-outline-on-select.
//
// The edge type name is registered on both the draft and snapshot
// ReactFlow instances via ``edgeTypes = { typed: TypedEdge }``.

import { memo, useCallback } from "react";
import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  type EdgeProps,
} from "@xyflow/react";

export interface TypedEdgeData extends Record<string, unknown> {
  /** Compact chip label (e.g. ``frame_sequence`` or ``arrayed<frame_sequence>``). */
  label?: string;
  /** Full label for the hover title so a truncated chip stays discoverable. */
  labelLong?: string;
}

const CHIP: React.CSSProperties = {
  position: "absolute",
  transform: "translate(-50%, -50%)",
  pointerEvents: "all",
  padding: "1px 6px",
  borderRadius: "var(--radius-pill)",
  background: "var(--surface-2)",
  border: "1px solid var(--border-subtle)",
  color: "var(--text-muted)",
  fontSize: "var(--fs-micro)",
  fontFamily: "var(--font-mono)",
  fontWeight: 500,
  letterSpacing: "0.01em",
  lineHeight: 1.4,
  whiteSpace: "nowrap",
  maxWidth: 140,
  overflow: "hidden",
  textOverflow: "ellipsis",
  userSelect: "none",
  // Quiet by default so overlapping wires don't produce a wall of chips.
  // Selection promotes to full opacity below.
  opacity: 0.72,
  transition:
    "opacity var(--dur-fast) var(--ease), background var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease), border-color var(--dur-fast) var(--ease)",
};

const CHIP_SELECTED: React.CSSProperties = {
  opacity: 1,
  background: "var(--accent-soft)",
  color: "var(--accent)",
  borderColor: "var(--accent)",
};

const STROKE_DEFAULT = 2;
const STROKE_SELECTED = 3;

type TypedEdgeProps = EdgeProps & {
  onSelect?: (id: string) => void;
};

function TypedEdgeInner({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  selected,
  data,
  markerEnd,
  onSelect,
}: TypedEdgeProps) {
  const [path, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });

  const d = data as TypedEdgeData | undefined;
  const label = d?.label ?? "";
  const labelLong = d?.labelLong ?? label;

  const stroke = selected ? "var(--accent)" : "var(--rf-edge, var(--border-strong))";
  const strokeWidth = selected ? STROKE_SELECTED : STROKE_DEFAULT;

  // The chip sits in an EdgeLabelRenderer portal — clicks on it do NOT
  // bubble to the underlying SVG edge, so xyflow's built-in
  // click-to-select is bypassed. Delegate to the controlled-state
  // callback so the App's edge array (and thus EdgeInspector) see the
  // selection change instead of only the internal xyflow store.
  const onChipClick = useCallback(
    (evt: React.MouseEvent) => {
      evt.stopPropagation();
      onSelect?.(id);
    },
    [onSelect, id],
  );

  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        markerEnd={markerEnd}
        style={{ stroke, strokeWidth, transition: "stroke var(--dur-fast) var(--ease)" }}
      />
      {label && (
        <EdgeLabelRenderer>
          <div
            className="hl-edge-chip"
            data-selected={selected ? "" : undefined}
            title={labelLong}
            onClick={onChipClick}
            style={{
              ...CHIP,
              left: labelX,
              top: labelY,
              cursor: "pointer",
              ...(selected ? CHIP_SELECTED : {}),
            }}
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

export const TypedEdge = memo(TypedEdgeInner);
