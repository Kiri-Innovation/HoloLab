import { expect, it } from "vitest";
import type { CatalogPack } from "../wire";
import { certificationMessage, packCertification } from "./packCertification";

const activeRelease = {
  state: "identical",
  source: "https://packs.xenotech.studio/",
  yanked: false,
  stale: false,
} satisfies CatalogPack["central"];

it.each(["k4d/stg-init", "community/foo", "another/foo"])(
  "certifies %s when its content matches an active official release",
  (name) => {
    expect(packCertification({ name, central: activeRelease })).toBe("community");
  },
);

it.each<[string, CatalogPack["central"]]>([
  ["different content", { ...activeRelease, state: "differs" }],
  ["unknown comparison", { ...activeRelease, state: "unknown" }],
  ["unpublished version", { state: "absent" }],
  ["yanked release", { ...activeRelease, yanked: true }],
  ["stale comparison", { ...activeRelease, stale: true }],
  ["untrusted registry", { ...activeRelease, source: "https://packs.xenotech.studio.example/" }],
  ["different registry root", { ...activeRelease, source: "https://packs.xenotech.studio/other/" }],
  ["missing registry source", { ...activeRelease, source: undefined }],
  ["missing yank status", { ...activeRelease, yanked: undefined }],
  ["missing freshness", { ...activeRelease, stale: undefined }],
  ["missing comparison", undefined],
])("omits certification for %s", (_reason, central) => {
  expect(packCertification({ name: "community/foo", central })).toBeNull();
});

it("certifies built-in packs without a registry release", () => {
  expect(packCertification({ name: "builtin/frame-extraction", central: { state: "absent" } }))
    .toBe("builtin");
});

it("explains why each certified distribution is reproducible", () => {
  expect(certificationMessage("builtin")).toBe("内置算法：随 HoloLab 提供，结果可复现");
  expect(certificationMessage("community")).toBe("社区发行版本：与注册表发布内容一致，结果可复现");
});
