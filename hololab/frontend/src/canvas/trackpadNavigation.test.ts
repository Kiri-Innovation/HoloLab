import { describe, expect, it } from "vitest";
import { TrackpadScrollDetector } from "./trackpadNavigation";

const wheel = (overrides: Partial<Pick<WheelEvent, "ctrlKey" | "deltaMode" | "deltaX" | "deltaY">> = {}) => ({
  ctrlKey: false,
  deltaMode: 0,
  deltaX: 0,
  deltaY: 0,
  ...overrides,
});

describe("TrackpadScrollDetector", () => {
  it("recognises small free-direction pixel scrolls and keeps their gesture latched", () => {
    const detector = new TrackpadScrollDetector();
    expect(detector.isTrackpadScroll(wheel({ deltaX: 16 }), 100)).toBe(true);
    expect(detector.isTrackpadScroll(wheel({ deltaY: -90 }), 150)).toBe(true);
    expect(detector.isTrackpadScroll(wheel({ deltaY: 100 }), 301)).toBe(false);
  });

  it("leaves discrete mouse wheels and Ctrl-wheel pinch to React Flow", () => {
    const detector = new TrackpadScrollDetector();
    expect(detector.isTrackpadScroll(wheel({ deltaY: 100 }), 100)).toBe(false);
    expect(detector.isTrackpadScroll(wheel({ deltaY: 12, ctrlKey: true }), 100)).toBe(false);
    expect(detector.isTrackpadScroll(wheel({ deltaY: 1, deltaMode: 1 }), 100)).toBe(false);
  });
});
