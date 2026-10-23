import { describe, it, expect } from "vitest";
import {
  activeAt,
  layerRect,
  moveTransform,
  resizeTransform,
  pixelRound,
  compositionError,
} from "./composeGeometry";
const clip = { id: "one", url: "x", width: 1920, height: 1080, duration: 3 };
describe("compose editor geometry", () => {
  it("uses contain, then scale, with top-left ratios", () => {
    expect(
      layerRect(clip, { width: 868, height: 1012 }, [0.727, 0.0198, 0.25]),
    ).toEqual({ x: 0.727 * 868, y: 0.0198 * 1012, width: 217, height: 122 });
  });
  it("converts displayed-pixel gestures independently of canvas zoom", () => {
    const a = moveTransform([0.1, 0.2, 0.25], 50, 25, 500, 250);
    expect(a[0]).toBeCloseTo(0.2);
    expect(a[1]).toBeCloseTo(0.3);
    expect(moveTransform([0.1, 0.2, 0.25], 25, 12.5, 250, 125)).toEqual(a);
  });
  it("resizes proportionally with a stable upper-left anchor", () => {
    expect(resizeTransform([0.1, 0.2, 0.25], 50, 25, 100, 50)).toEqual([
      0.1, 0.2, 0.375,
    ]);
  });
  it("matches renderer half-to-even rounding", () => {
    expect(pixelRound(122.5)).toBe(122);
    expect(pixelRound(123.5)).toBe(124);
  });
  it("uses half-open visibility windows", () => {
    expect(activeAt(0.5, 2.5, 3)).toBe(false);
    expect(activeAt(3, 2.5, 3)).toBe(true);
    expect(activeAt(5.5, 2.5, 3)).toBe(false);
    expect(activeAt(10, 2.5, 3)).toBe(false);
  });
  it("reports missing/mismatched and invalid attributes without substituting defaults", () => {
    expect(compositionError([], [], 2)).toContain("transforms");
    expect(compositionError([[0, 0, 1]], [-1], 1)).toContain("offset");
    expect(compositionError([[0, 0, 0]], [0], 1)).toContain("scale");
    expect(compositionError([[0, 0, 1]], [0], 1)).toBeNull();
  });
});
