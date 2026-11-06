/** Device identity cannot be inferred reliably from wheel deltas or timing.
 * An explicit mode keeps every scroll sequence on a stable navigation path.
 * Only Ctrl-wheel (pinch) retains React Flow's default zoom path.
 */
export function shouldPanWheel(enabled: boolean, event: Pick<WheelEvent, "ctrlKey">): boolean {
  return enabled && !event.ctrlKey;
}

/** Elements whose existing wheel contract must take precedence over canvas panning. */
export function keepsExistingWheelBehavior(target: EventTarget | null): boolean {
  return target instanceof Element
    && Boolean(target.closest(".nowheel, .nopan, .hl-preview-drawer"));
}
