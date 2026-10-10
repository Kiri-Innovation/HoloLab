import { expect, it } from "vitest";
import { tooltipGeometry } from "./tooltipGeometry";

const viewport = { width: 1000, height: 700 };
const bubble = { width: 120, height: 30 };
it.each([0.5, 1, 1.5])("positions the scaled tooltip and tail at zoom %s", zoom => {
  const size = { width: bubble.width * zoom, height: bubble.height * zoom };
  const result = tooltipGeometry({ left: 400, top: 200, width: 24 * zoom, bottom: 200 + 24 * zoom }, size, viewport, zoom);
  expect(result.placement).toBe("above");
  expect(result.top + size.height + 8 * zoom).toBe(200);
  expect(result.left + result.arrowX).toBe(400 + 12 * zoom);
});
it("flips below near the top edge", () => {
  const result = tooltipGeometry({ left: 400, top: 4, width: 24, bottom: 28 }, bubble, viewport, 1);
  expect(result.placement).toBe("below");
  expect(result.top).toBe(36);
});
it.each([0, 976])("offsets the tail when horizontally clamped at %s", left => {
  const result = tooltipGeometry({ left, top: 200, width: 24, bottom: 224 }, bubble, viewport, 1);
  expect(result.left).toBeGreaterThanOrEqual(8);
  expect(result.left + bubble.width).toBeLessThanOrEqual(992);
  expect(result.left + result.arrowX).toBe(left + 12);
  expect(result.arrowX).not.toBe(bubble.width / 2);
});

it.each([0.5, 1, 1.5])("left-aligns title cards with tail clearance at zoom %s", zoom => {
  const result = tooltipGeometry({ left: 100, top: 300, width: 100 * zoom, bottom: 330 }, bubble, viewport, zoom, { align: "left", gap: 13 });
  expect(result.left).toBe(100);
  expect(result.arrowX).toBe(12 * zoom);
  expect(300 - result.top - bubble.height).toBe(13 * zoom);
});
it("keeps left-aligned cards inside the right edge and tracks the title", () => {
  const result = tooltipGeometry({ left: 930, top: 300, width: 50, bottom: 330 }, bubble, viewport, 1, { align: "left", gap: 13 });
  expect(result.left + bubble.width).toBe(992);
  expect(result.left + result.arrowX).toBe(942);
});
