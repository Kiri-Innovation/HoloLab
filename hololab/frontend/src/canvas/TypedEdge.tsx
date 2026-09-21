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
//   * Node avoidance: the default t=0.5 chip position sometimes falls
//     right on top of a node card (long horizontal edges routed through
//     a mid-column node). We sample the bezier at t values fanning
//     outward from 0.5 and use the first spot whose chip bbox clears
//     every node's bbox in flow-space. If no inline spot is clear the
//     chip stays at t=0.5 but flips to "covered" mode — lifted above
//     the wire with a down-pointing anchor arrow (visually the same as
//     the short-edge pop, but auto-triggered independent of hover).
//
// All chip visual properties live in styles.css (.hl-edge-chip) so the
// dot-mode attribute selectors can override them without fighting
// inline-style specificity.
//
// The edge type name is registered on both the draft and snapshot
// ReactFlow instances via ``edgeTypes = { typed: TypedEdge }``.

import { memo, useCallback, useEffect, useMemo, useState } from "react";
import {
  BaseEdge,
  EdgeLabelRenderer,
  Position,
  getBezierPath,
  useStore,
  type EdgeProps,
  type ReactFlowState,
} from "@xyflow/react";
import type { EdgeType } from "./edgeLabels";
import { formatTypeLabel, formatTypeLabelLong } from "./edgeLabels";
import {
  loadEdgeSummaryFacts,
  peekEdgeSummaryFacts,
  type EdgeSummaryFacts,
} from "./edgeSummaryCache";

export interface TypedEdgeData extends Record<string, unknown> {
  /** Compact chip label without runtime counts (e.g. ``colmap`` or
   *  ``colmap[?]``). TypedEdge re-formats with counts once hovered. */
  label?: string;
  /** Full label for the hover title so a truncated chip stays discoverable. */
  labelLong?: string;
  /** Full type descriptor (tags + arrayed depth + dim labels). Needed
   *  by TypedEdge to re-compose the label with the runtime numbers
   *  once ``loadEdgeSummaryFacts`` returns. */
  edgeType?: EdgeType;
  /** Source handle id, when the producing job has emitted an output
   *  handle for this edge. Present in both draft (resolved from
   *  ``previewsByGraphNode``) and snapshot (from ``output_handles``)
   *  views. Absent when the source hasn't produced a handle yet — the
   *  chip stays at ``[?]``. */
  handleId?: string | null;
}

// EdgeLabelRenderer renders inside the viewport's CSS transform, so the
// chip's CSS pixels are in graph-coordinate units (same space as
// sourceX/targetX).  We compare dx directly to the chip's maxWidth
// (140 px) plus a margin; below this the chip would overflow onto the
// adjacent node cards at any zoom level.
const DOT_THRESHOLD = 160;

const STROKE_DEFAULT = 2;
const STROKE_SELECTED = 3;

// Chip bbox estimates in flow-space (same units as node rects — both
// live under the viewport CSS transform). Full chip: pill padding
// 1+6+6+1 = 14, mono char ~6.5 px at fs-micro. Height covers padding
// + line-height at fs-micro. Dot: 8 px + 2 px margin each side.
const CHIP_H = 22;
const CHIP_CHAR = 6.5;
const CHIP_PAD = 14;
const CHIP_MAX_W = 140;
const DOT_BOX = 12;

// Sample t-values fanning outward from the natural midpoint. Ordered
// so ties prefer the inline-most position (closest to t=0.5) — this
// keeps the chip visually anchored to the wire's middle whenever the
// midpoint itself is clear.
const T_SAMPLES = [0.5, 0.42, 0.58, 0.34, 0.66, 0.26, 0.74, 0.18, 0.82];

// Sane pop-lift when the covering node's rect isn't measured yet.
const POP_LIFT_DEFAULT = 32;

// --- flow-space rect helpers ---

type Rect = { x: number; y: number; w: number; h: number };

function intersects(a: Rect, b: Rect): boolean {
  return !(
    a.x + a.w <= b.x ||
    b.x + b.w <= a.x ||
    a.y + a.h <= b.y ||
    b.y + b.h <= a.y
  );
}

// Replicates @xyflow/system's control-offset formula so our sampled
// bezier matches the rendered SVG path (and t=0.5 matches labelX/labelY).
function controlOffset(distance: number, curvature: number): number {
  if (distance >= 0) return 0.5 * distance;
  return curvature * 25 * Math.sqrt(-distance);
}

function controlPoint(
  pos: Position,
  x1: number,
  y1: number,
  x2: number,
  y2: number,
  c: number,
): [number, number] {
  switch (pos) {
    case Position.Left:
      return [x1 - controlOffset(x1 - x2, c), y1];
    case Position.Right:
      return [x1 + controlOffset(x2 - x1, c), y1];
    case Position.Top:
      return [x1, y1 - controlOffset(y1 - y2, c)];
    case Position.Bottom:
      return [x1, y1 + controlOffset(y2 - y1, c)];
  }
}

function cubicBezier(
  t: number,
  x0: number,
  y0: number,
  x1: number,
  y1: number,
  x2: number,
  y2: number,
  x3: number,
  y3: number,
): [number, number] {
  const u = 1 - t;
  const uu = u * u;
  const tt = t * t;
  const uuu = uu * u;
  const ttt = tt * t;
  const a = 3 * uu * t;
  const b = 3 * u * tt;
  return [
    uuu * x0 + a * x1 + b * x2 + ttt * x3,
    uuu * y0 + a * y1 + b * y2 + ttt * y3,
  ];
}

// Selector returns the nodeLookup Map. Same reference between store
// updates that don't touch node internals — so a pan/zoom-only viewport
// change won't cause the edge to re-avoid.
const selectNodeLookup = (s: ReactFlowState) => s.nodeLookup;

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
  const baseLabel = d?.label ?? "";
  const baseLabelLong = d?.labelLong ?? baseLabel;
  const edgeType = d?.edgeType;
  const handleId = d?.handleId ?? null;

  // Cached summary facts. Seeded synchronously from the module-level
  // cache so a re-mounted edge (theme flip, panel resize) reuses the
  // last resolved value without re-fetching or reflashing to ``[?]``.
  const [facts, setFacts] = useState<EdgeSummaryFacts | undefined>(() =>
    handleId ? peekEdgeSummaryFacts(handleId) : undefined,
  );

  // Reset the cached facts when the underlying handle changes (edge
  // re-wire, snapshot switch). A stale ``elementCount`` from the old
  // handle would otherwise stick to the new one until first hover.
  useEffect(() => {
    setFacts(handleId ? peekEdgeSummaryFacts(handleId) : undefined);
  }, [handleId]);

  // Kick off the fetch the first time the edge is hovered — but only
  // when the source handle actually exists. Deduped by
  // ``loadEdgeSummaryFacts`` so a wildly re-hovered edge fires one HTTP
  // request over the session. Ignored errors resolve to empty facts.
  useEffect(() => {
    if (!edgeHovered || !handleId || facts !== undefined) return;
    let cancelled = false;
    loadEdgeSummaryFacts(handleId).then((next) => {
      if (!cancelled) setFacts(next);
    });
    return () => {
      cancelled = true;
    };
  }, [edgeHovered, handleId, facts]);

  // Compose the chip and tooltip strings. When ``edgeType`` is missing
  // (older edges built before we started stashing it) fall back to the
  // pre-formatted ``label`` — no runtime numbers, but nothing regresses.
  const { label, labelLong } = useMemo(() => {
    if (!edgeType) return { label: baseLabel, labelLong: baseLabelLong };
    const enriched: EdgeType = {
      ...edgeType,
      // Runtime dim_labels from the handle summary overrides the static
      // catalog value.  Generic ports (regroup.out) have dim_labels=[]
      // in the catalog; the summary carries the resolved param value.
      dimLabels:
        facts?.dimLabels && facts.dimLabels.length > 0
          ? facts.dimLabels
          : edgeType.dimLabels,
      dimSizes: facts?.dimSizes ?? edgeType.dimSizes,
      elementCount: facts?.elementCount ?? edgeType.elementCount,
      innerElementCount:
        facts?.innerElementCount ?? edgeType.innerElementCount,
      internalCount: facts?.internalCount ?? edgeType.internalCount,
      internalCountKind:
        facts?.internalCountKind ?? edgeType.internalCountKind,
      internalCountItems:
        facts?.internalCountItems ?? edgeType.internalCountItems,
    };
    return {
      label: formatTypeLabel(enriched),
      labelLong: formatTypeLabelLong(enriched),
    };
  }, [edgeType, facts, baseLabel, baseLabelLong]);

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

  // Chip bbox estimate for avoidance. Dot uses the small 12 px box; full
  // chip uses actual label-width estimate (capped at CSS max-width).
  const chipW = useDot
    ? DOT_BOX
    : Math.min(CHIP_MAX_W, CHIP_PAD + label.length * CHIP_CHAR);
  const chipH = useDot ? DOT_BOX : CHIP_H;

  // Node bounds in flow-space, pulled from xyflow's internal store.
  // ``nodeLookup`` is only re-issued when a node's internals change, so
  // panning/zooming doesn't invalidate this hook.
  const nodeLookup = useStore(selectNodeLookup);
  const nodeRects = useMemo(() => {
    const rects: Rect[] = [];
    nodeLookup.forEach((n) => {
      const pos = n.internals?.positionAbsolute ?? n.position;
      const w = n.measured?.width ?? n.width ?? 0;
      const h = n.measured?.height ?? n.height ?? 0;
      if (w > 0 && h > 0 && pos) {
        rects.push({ x: pos.x, y: pos.y, w, h });
      }
    });
    return rects;
  }, [nodeLookup]);

  // --- avoidance search ---
  // Sample the bezier at t values fanning outward from 0.5. First
  // point whose chip bbox clears every node wins. If none clear, stay
  // at (labelX, labelY) and flip to "covered" mode; the caller lifts
  // the chip vertically with an anchor arrow to keep it attached to
  // the wire visually.
  const chipRect = useCallback(
    (cx: number, cy: number): Rect => ({
      x: cx - chipW / 2,
      y: cy - chipH / 2,
      w: chipW,
      h: chipH,
    }),
    [chipW, chipH],
  );

  const isCovered = useCallback(
    (cx: number, cy: number): Rect | null => {
      const r = chipRect(cx, cy);
      for (const nr of nodeRects) {
        if (intersects(r, nr)) return nr;
      }
      return null;
    },
    [chipRect, nodeRects],
  );

  const { chipX, chipY, covered, popLift } = useMemo(() => {
    // Fast path: default midpoint is clear — no need to touch controls
    // or sample. Runs for the majority of edges.
    if (!isCovered(labelX, labelY)) {
      return { chipX: labelX, chipY: labelY, covered: false, popLift: 0 };
    }
    const [c1x, c1y] = controlPoint(
      sourcePosition,
      sourceX,
      sourceY,
      targetX,
      targetY,
      0.25,
    );
    const [c2x, c2y] = controlPoint(
      targetPosition,
      targetX,
      targetY,
      sourceX,
      sourceY,
      0.25,
    );
    for (const t of T_SAMPLES) {
      if (t === 0.5) continue; // already tried
      const [x, y] = cubicBezier(
        t,
        sourceX,
        sourceY,
        c1x,
        c1y,
        c2x,
        c2y,
        targetX,
        targetY,
      );
      if (!isCovered(x, y)) {
        return { chipX: x, chipY: y, covered: false, popLift: 0 };
      }
    }
    // Nothing clear along the mid-region of the bezier — fall back to
    // "covered" mode. Compute the lift needed to clear the worst-
    // covering node above the label point so the popped chip actually
    // escapes the card (a hard-coded 28 px isn't enough when a wire
    // runs through a tall node).
    const covering = isCovered(labelX, labelY);
    const lift = covering
      ? Math.max(POP_LIFT_DEFAULT, labelY - covering.y + chipH / 2 + 8)
      : POP_LIFT_DEFAULT;
    return { chipX: labelX, chipY: labelY, covered: true, popLift: lift };
  }, [
    labelX,
    labelY,
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    isCovered,
    chipH,
  ]);

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

  // Both the short-edge hover pop and the auto "covered" avoidance use
  // the same data-pop visual (lifted + arrow + z-index bump). Merging
  // them keeps the CSS surface small and the visual language consistent.
  const showPop = dotExpanded || covered;
  const chipStyle: React.CSSProperties = { left: chipX, top: chipY };
  if (covered) {
    (chipStyle as React.CSSProperties & Record<string, string>)[
      "--pop-lift"
    ] = `${popLift}px`;
  }

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
            data-pop={showPop ? "" : undefined}
            data-covered={covered ? "" : undefined}
            title={labelLong}
            onClick={onChipClick}
            onMouseEnter={onChipEnter}
            style={chipStyle}
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

export const TypedEdge = memo(TypedEdgeInner);
