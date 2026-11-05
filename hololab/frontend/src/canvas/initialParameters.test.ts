import { expect, it } from "vitest";
import type { CatalogPack } from "../wire";
import { initialParameters } from "./initialParameters";

it("persists manifest defaults at creation without sharing mutable values", () => {
  const pack = { params: {
    skip_image_validation: { type: "bool", default: true },
    disabled: { type: "bool", default: false },
    start: { type: "int", default: 0 },
    text: { type: "string", default: "" },
    values: { type: "array", default: [] },
    required: { type: "path", default: null },
  } } as unknown as CatalogPack;
  const first = initialParameters(pack);
  expect(first).toEqual({ skip_image_validation: true, disabled: false, start: 0, text: "", values: [] });
  (first.values as number[]).push(1);
  expect(initialParameters(pack).values).toEqual([]);
  expect(pack.params.values.default).toEqual([]);
});
