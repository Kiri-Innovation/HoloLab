/** Device identity cannot be inferred reliably from wheel deltas or timing.
 * An explicit mode keeps every scroll sequence on a stable navigation path.
 * Only Ctrl-wheel (pinch) retains React Flow's default zoom path.
 */
export function shouldPanWheel(enabled: boolean, event: Pick<WheelEvent, "ctrlKey">): boolean {
  return enabled && !event.ctrlKey;
}

/** Elements whose existing wheel contract must take precedence over canvas panning. */
export function keepsExistingWheelBehavior(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  const protectedAncestor = target.closest(".nowheel, .nopan, .hl-preview-drawer");
  // XYFlow adds `nopan` to every draggable node wrapper. That marker only
  // controls drag-to-pan, not the node card's wheel contract; nested `nopan`
  // controls (including pinned parameters) still retain their own behavior.
  return Boolean(protectedAncestor && !protectedAncestor.matches(".react-flow__node.nopan"));
}
