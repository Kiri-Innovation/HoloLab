/** Device identity cannot be inferred reliably from wheel deltas or timing.
 * An explicit mode keeps every scroll sequence on a stable navigation path.
 * Only Ctrl-wheel (pinch) retains React Flow's default zoom path.
 */
export function shouldPanWheel(enabled: boolean, event: Pick<WheelEvent, "ctrlKey">): boolean {
  return enabled && !event.ctrlKey;
}

/** Only an explicit local owner may opt out of canvas wheel navigation.
 * Drag classes (`nopan` / `nodrag`) deliberately have no wheel meaning here.
 * Keep `nowheel` on owners which also need XYFlow's native zoom isolation.
 */
export function keepsExistingWheelBehavior(target: EventTarget | null): boolean {
  return target instanceof Element && Boolean(target.closest('[data-wheel-owner="local"]'));
}
