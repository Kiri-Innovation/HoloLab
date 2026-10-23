import { describe, expect, it } from "vitest";
import { repairComposeValue as repair } from "./composeRepairs";
import { compositionError } from "./composeGeometry";

describe("compose authoring repairs", () => {
  it("repairs scale, missing layers and negative offsets into runnable inputs", () => {
    const t = repair("transforms", [[0, 0, 0]], 2);
    const o = repair("offsets", [0, -1, 99], 2);
    expect(t.value).toEqual([
      [0, 0, 1],
      [0, 0, 1],
    ]);
    expect(o.value).toEqual([0, 0]);
    expect(t.changes).toHaveLength(2);
    expect(o.changes).toHaveLength(2);
    expect(compositionError(t.value, o.value, 2)).toBeNull();
  });
  it("preserves all valid values including out-of-canvas coordinates and fractional offsets", () => {
    const t = [
      [-3, 8, 0.2],
      [0, 0, 1],
    ];
    expect(repair("transforms", t, 2)).toEqual({ value: t, changes: [] });
    expect(repair("offsets", [0, 0.125], 2).changes).toEqual([]);
  });
  it("repairs malformed rows and finite-number format without coercing strings or booleans", () => {
    expect(
      repair("transforms", [[NaN, -4, Infinity, 7], null], 2).value,
    ).toEqual([
      [0, -4, 1],
      [0, 0, 1],
    ]);
    expect(repair("offsets", [false, "3", Infinity], 3).value).toEqual([
      0, 0, 0,
    ]);
  });
  it("is idempotent and reports truncation and each inserted row", () => {
    for (const port of ["transforms", "offsets"] as const) {
      const first = repair(port, undefined, 2);
      expect(first.changes).toHaveLength(3);
      expect(repair(port, first.value, 2).changes).toEqual([]);
      expect(repair(port, first.value, 1).changes).toHaveLength(1);
    }
  });
});
