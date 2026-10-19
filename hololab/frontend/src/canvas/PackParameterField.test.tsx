import { expect, it } from "vitest";
import type { CatalogPack } from "../wire";
import { PackParameterField, packParameterRows, mergeParameterPatch } from "./PackParameterField";
import { ParameterInput, Vec3ArrayParameterInput } from "./ParameterInput";
const pack = { name: "demo", version: "1", params: { choice: { type: "enum", values: [1, 2], default: 1 } } } as unknown as CatalogPack;
it("keeps numerical enum choices and emits a field patch", () => {
  let patch: unknown;
  const field = PackParameterField({ pack, params: {}, name: "choice", names: ["choice"], nodeId: null, initialDirectory: "/", onChange: next => { patch = next; } });
  expect(field.type).toBe(ParameterInput);
  expect(field.props.value).toBe(1);
  field.props.onChange(2);
  expect(patch).toEqual({ choice: 2 });
});
it("merges against current values without overwriting unrelated edits", () => {
  const first = mergeParameterPatch(pack, { choice: 1, text: "old" }, { text: "new" });
  expect(mergeParameterPatch(pack, first, { choice: 2 })).toEqual({ choice: 2, text: "new" });
});
it("shares transform grouping and the same editor", () => {
  const vec = { name: "vec3-array-source", version: "0.1.0", params: Object.fromEntries(["x", "y", "scale"].map(k => [k, { type: "array", items: "float", default: [] }])) } as unknown as CatalogPack;
  const rows = packParameterRows(vec, {});
  expect(rows).toHaveLength(1);
  expect(rows[0].names).toEqual(["x", "y", "scale"]);
  expect(PackParameterField({ pack: vec, params: {}, ...rows[0], nodeId: null, initialDirectory: "/", onChange: () => {} }).type).toBe(Vec3ArrayParameterInput);
});
