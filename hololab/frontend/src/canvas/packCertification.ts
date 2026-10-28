import { canonicalPackageName } from "../packageIdentity";
import type { CatalogPack } from "../wire";

export type PackCertification = "builtin" | "community" | null;

/** Built-in packs are shipped with HoloLab; community packs require registry parity. */
export function packCertification(pack: Pick<CatalogPack, "name" | "central">): PackCertification {
  const name = canonicalPackageName(pack.name);
  if (name.startsWith("builtin/")) return "builtin";
  if (name.startsWith("community/") && pack.central?.state === "identical") return "community";
  return null;
}

export function certificationMessage(certification: Exclude<PackCertification, null>): string {
  return certification === "builtin"
    ? "内置算法：随 HoloLab 提供，结果可复现"
    : "社区发行版本：与注册表发布内容一致，结果可复现";
}
