/** Device identity cannot be inferred reliably from wheel deltas or timing.
 * An explicit mode keeps every scroll sequence on a stable navigation path.
 * Ctrl-wheel (pinch) and line/page wheels retain React Flow's default path.
 */
export function shouldPanWheel(enabled: boolean, event: Pick<WheelEvent, "ctrlKey" | "deltaMode">): boolean {
  return enabled && !event.ctrlKey && event.deltaMode === 0;
}

/** Elements whose existing wheel contract must take precedence over canvas panning. */
export function keepsExistingWheelBehavior(target: EventTarget | null): boolean {
  return target instanceof Element
    && Boolean(target.closest(".nowheel, .nopan, .hl-preview-drawer"));
}
