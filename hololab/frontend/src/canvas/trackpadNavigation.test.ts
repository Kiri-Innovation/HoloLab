import { describe, expect, it } from "vitest";
import { shouldPanWheel } from "./trackpadNavigation";

const wheel = (overrides: Partial<Pick<WheelEvent, "ctrlKey" | "deltaMode" | "deltaX" | "deltaY">> = {}) => ({
  ctrlKey: false,
  deltaMode: 0,
  deltaX: 0,
  deltaY: 0,
  ...overrides,
});

describe("explicit trackpad navigation", () => {
  it("uses the selected mode for every delta magnitude", () => {
    expect(shouldPanWheel(false, wheel({ deltaX: 16 }))).toBe(false);
    expect(shouldPanWheel(false, wheel({ deltaY: 1 }))).toBe(false);
    expect(shouldPanWheel(true, wheel({ deltaY: -90 }))).toBe(true);
    expect(shouldPanWheel(true, wheel({ deltaY: 100 }))).toBe(true);
  });

  it("leaves mouse mode, Ctrl-wheel pinch and line wheels to React Flow", () => {
    expect(shouldPanWheel(false, wheel({ deltaY: 100 }))).toBe(false);
    expect(shouldPanWheel(true, wheel({ deltaY: 12, ctrlKey: true }))).toBe(false);
    expect(shouldPanWheel(true, wheel({ deltaY: 1, deltaMode: 1 }))).toBe(false);
  });
});
