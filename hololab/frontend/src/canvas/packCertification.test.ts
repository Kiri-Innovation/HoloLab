import { expect, it } from "vitest";
import type { CatalogPack } from "../wire";
import { certificationMessage, packCertification } from "./packCertification";

it.each([
  ["builtin/video-array-source", { state: "absent" }, "builtin"],
  ["community/colmap-sfm", { state: "identical" }, "community"],
  ["frame-extraction", { state: "identical" }, null],
  ["community/colmap-sfm", { state: "differs" }, null],
] as const)("classifies %s by its reproducible distribution", (name, central, expected) => {
  expect(packCertification({ name, central } as Pick<CatalogPack, "name" | "central">)).toBe(expected);
});

it("explains why each certified distribution is reproducible", () => {
  expect(certificationMessage("builtin")).toBe("内置算法：随 HoloLab 提供，结果可复现");
  expect(certificationMessage("community")).toBe("社区发行版本：与注册表发布内容一致，结果可复现");
});
