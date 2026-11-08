/** Device identity cannot be inferred reliably from wheel deltas or timing.
 * An explicit mode keeps every scroll sequence on a stable navigation path.
 * Only Ctrl-wheel (pinch) retains React Flow's default zoom path.
 */
export function shouldPanWheel(enabled: boolean, event: Pick<WheelEvent, "ctrlKey">): boolean {
  return enabled && !event.ctrlKey;
}

/** Elements whose existing wheel contract must take precedence over canvas panning. */
export function keepsExistingWheelBehavior(target: EventTarget | null, trackpadMode: boolean): boolean {
  if (!(target instanceof Element)) return false;
  // Preview drawers follow the selected canvas navigation mode. Embedded
  // viewers and parameter controls keep owning their wheel events in either mode.
  if (target.closest(".nowheel")) return true;
  if (!trackpadMode && target.closest(".hl-preview-drawer")) return true;
  const protectedAncestor = target.closest(".nopan");
  // XYFlow adds `nopan` to every draggable node wrapper. That marker only
  // controls drag-to-pan, not the node card's wheel contract; nested `nopan`
  // controls (including pinned parameters) still retain their own behavior.
  return Boolean(protectedAncestor && !protectedAncestor.matches(".react-flow__node.nopan"));
}
