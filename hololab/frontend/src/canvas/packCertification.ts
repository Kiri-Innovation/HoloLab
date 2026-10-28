import { canonicalPackageName } from "../packageIdentity";
import type { CatalogPack } from "../wire";

/** Built-in packs are shipped with HoloLab; community packs require registry parity. */
export function isCertifiedPack(pack: Pick<CatalogPack, "name" | "central">): boolean {
  const name = canonicalPackageName(pack.name);
  return name.startsWith("builtin/") ||
    (name.startsWith("community/") && pack.central?.state === "identical");
}
