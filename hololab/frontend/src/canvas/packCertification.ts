import { canonicalPackageName } from "../packageIdentity";
import type { CatalogPack } from "../wire";

export type PackCertification = "builtin" | "community" | null;

// Keep in sync with OFFICIAL_REGISTRY in hololab/pack_registry.py.
const OFFICIAL_REGISTRY = "https://packs.xenotech.studio/";

/** Built-in packs ship with HoloLab. Other packs must match an active official release. */
export function packCertification(pack: Pick<CatalogPack, "name" | "central">): PackCertification {
  const name = canonicalPackageName(pack.name);
  if (name.startsWith("builtin/")) return "builtin";
  // The gateway compares the local and registry hashes for the same canonical name and version.
  const { central } = pack;
  if (
    central?.state === "identical" &&
    central.source === OFFICIAL_REGISTRY &&
    central.yanked === false &&
    central.stale === false
  ) return "community";
  return null;
}

export function certificationMessage(certification: Exclude<PackCertification, null>): string {
  return certification === "builtin"
    ? "内置算法：随 HoloLab 提供，结果可复现"
    : "社区发行版本：与注册表发布内容一致，结果可复现";
}
