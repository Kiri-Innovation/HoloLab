// Custom xyflow edge that renders a small "data type" chip near the
// midpoint of the wire and highlights when selected.
//
// Design:
//   * The chip carries the source port's effective type (tag + arrayed
//     form — same vocabulary as the port dot's ``data-tags`` and the
//     inspector). The label string is computed by the enclosing canvas
//     and stashed on ``edge.data`` so this component stays a pure
//     renderer (no catalog lookups here).
//   * Long-span edges (dx ≥ DOT_THRESHOLD) show a quiet full chip at
//     all times; hover and selection bump it to full opacity/accent.
//   * Short-span edges (near-vertical wires) collapse to an 8 px dot
//     so the chip doesn't overflow onto adjacent node cards. The dot
//     expands on chip hover (:hover CSS) or when the SVG edge path is
//     hovered (JS state) or when the edge is selected.
//
// All chip visual properties live in styles.css (.hl-edge-chip) so the
// dot-mode attribute selectors can override them without fighting
// inline-style specificity.
//
// The edge type name is registered on both the draft and snapshot
// ReactFlow instances via ``edgeTypes = { typed: TypedEdge }``.

import { memo, useCallback, useState } from "react";
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

// EdgeLabelRenderer renders inside the viewport's CSS transform, so the
// chip's CSS pixels are in graph-coordinate units (same space as
// sourceX/targetX).  We compare dx directly to the chip's maxWidth
// (140 px) plus a margin; below this the chip would overflow onto the
// adjacent node cards at any zoom level.
const DOT_THRESHOLD = 160;

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
  // Track edge-path hover so the dot expands even before the user
  // reaches the tiny chip (via a 20 px transparent hit path below).
  // Also set by the chip's own onMouseEnter so hovering the dot directly
  // triggers the JS-driven pop (prevents CSS-transform flicker where the
  // chip would move away from the cursor and immediately un-hover).
  const [edgeHovered, setEdgeHovered] = useState(false);

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

  // Dot mode: short-span edge and not currently expanded.
  // All expansion is JS-driven (onMouseEnter on both the fat hit-path and
  // the chip itself) so the chip's transform change can't cause CSS-only
  // hover to flicker.
  const useDot = label !== "" && Math.abs(targetX - sourceX) < DOT_THRESHOLD;
  const dotCollapsed = useDot && !selected && !edgeHovered;
  // Pop state: short-span edge in expanded form — chip floats above the line.
  const dotExpanded = useDot && (selected || edgeHovered);

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

  // When the user hovers directly on the collapsed dot, promote to the JS
  // edgeHovered state so the pop is JS-driven (not CSS-only).  This prevents
  // the chip from flickering: if CSS alone moved the chip up, the cursor
  // would immediately leave the chip's new position and collapse it again.
  const onChipEnter = useCallback(() => setEdgeHovered(true), []);

  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        markerEnd={markerEnd}
        style={{ stroke, strokeWidth, transition: "stroke var(--dur-fast) var(--ease)" }}
      />
      {/* Transparent fat path — 20 px hit area so the user can hover the
          wire without pixel-hunting; triggers the dot → chip expansion. */}
      <path
        d={path}
        strokeWidth={20}
        stroke="transparent"
        fill="none"
        onMouseEnter={() => setEdgeHovered(true)}
        onMouseLeave={() => setEdgeHovered(false)}
      />
      {label && (
        <EdgeLabelRenderer>
          <div
            className="hl-edge-chip"
            data-selected={selected ? "" : undefined}
            data-dot={dotCollapsed ? "" : undefined}
            data-pop={dotExpanded ? "" : undefined}
            title={labelLong}
            onClick={onChipClick}
            onMouseEnter={onChipEnter}
            style={{ left: labelX, top: labelY }}
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

export const TypedEdge = memo(TypedEdgeInner);
