import { omitEmptyOptionalPaths } from "./optionalPaths";
import type { CatalogPack } from "../wire";

/** Persist declared defaults when creating a node, including false and zero. */
export function initialParameters(pack: Pick<CatalogPack, "params">): Record<string, unknown> {
  return omitEmptyOptionalPaths(pack.params, Object.fromEntries(Object.entries(pack.params)
    .filter(([, spec]) => spec.default !== undefined && spec.default !== null)
    .map(([name, spec]) => [name, structuredClone(spec.default)])));
}
