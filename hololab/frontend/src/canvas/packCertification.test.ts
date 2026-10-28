import { expect, it } from "vitest";
import type { CatalogPack } from "../wire";
import { isCertifiedPack } from "./packCertification";

it.each([
  ["builtin/video-array-source", { state: "absent" }, true],
  ["community/colmap-sfm", { state: "identical" }, true],
  ["frame-extraction", { state: "identical" }, false],
  ["community/colmap-sfm", { state: "differs" }, false],
] as const)("certifies %s only when its distribution is trusted", (name, central, expected) => {
  expect(isCertifiedPack({ name, central } as Pick<CatalogPack, "name" | "central">)).toBe(expected);
});
