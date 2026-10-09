export interface TooltipAnchor {
  left: number;
  top: number;
  width: number;
  bottom: number;
}

/** Screen-space placement after measuring the zoom-sized text bubble. */
export function tooltipGeometry(
  anchor: TooltipAnchor,
  bubble: { width: number; height: number },
  viewport: { width: number; height: number },
  zoom: number,
) {
  const center = anchor.left + anchor.width / 2;
  const gap = 8 * zoom; // includes the 5*zoom tail and a small gap to the button
  const above = anchor.top - bubble.height - gap;
  const placement = above >= 8 ? "above" : "below";
  const left = Math.max(8, Math.min(center - bubble.width / 2, viewport.width - bubble.width - 8));
  return {
    left,
    top: Math.max(8, Math.min(placement === "above" ? above : anchor.bottom + gap, viewport.height - bubble.height - 8)),
    placement,
    // Do not re-center after edge avoidance: the tail tracks the button.
    arrowX: center - left,
  };
}
