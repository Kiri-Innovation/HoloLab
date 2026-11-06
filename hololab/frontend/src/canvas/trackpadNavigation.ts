/**
 * WheelEvent deliberately has no "this came from a trackpad" field.  The
 * pixel-sized deltas emitted by a trackpad are nevertheless distinguishable
 * from the large, discrete deltas of the conventional mouse wheels we
 * support.  Keep that policy in one small, testable place.
 */
export class TrackpadScrollDetector {
  private activeUntil = 0;

  isTrackpadScroll(event: Pick<WheelEvent, "ctrlKey" | "deltaMode" | "deltaX" | "deltaY">, now: number): boolean {
    // Chrome reports a macOS pinch as ctrl+wheel. React Flow already has the
    // right native pinch implementation, so it must never be converted to a
    // pan here.
    // DOM_DELTA_PIXEL is specified as 0. Spelling it as a literal also keeps
    // this policy usable in Node-only unit tests where WheelEvent is absent.
    if (event.ctrlKey || event.deltaMode !== 0) return false;

    const magnitude = Math.max(Math.abs(event.deltaX), Math.abs(event.deltaY));
    const looksLikeTrackpad = event.deltaX !== 0 || magnitude < 50;
    if (looksLikeTrackpad) this.activeUntil = now + 200;
    return looksLikeTrackpad || now < this.activeUntil;
  }
}

/** Elements whose existing wheel contract must take precedence over canvas panning. */
export function keepsExistingWheelBehavior(target: EventTarget | null): boolean {
  return target instanceof Element
    && Boolean(target.closest(".nowheel, .nopan, .hl-preview-drawer"));
}
