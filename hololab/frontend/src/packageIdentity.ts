/** Comparison only: persisted graph and historical spellings are not rewritten. */
export const canonicalPackageName = (name: string) => name && !name.includes('/') ? `kiri/${name}` : name;
export const packageKey = (name: string, version: string) => `${canonicalPackageName(name)}@${version}`;
export const samePackage = (a: string, av: string, b: string, bv: string) => packageKey(a, av) === packageKey(b, bv);
export function packageAliases(name: string): string[] {
  const canonical = canonicalPackageName(name);
  return [...new Set([name, canonical, ...(canonical.startsWith('kiri/') ? [canonical.slice(5)] : [])])];
}
/** Each lookup retains the requested spelling, so editing old graphs cannot rename them. */
export function packageCatalogIndex<T extends {name: string; version: string}>(packs: T[]): Map<string, T> {
  return new Map(packs.flatMap(pack => packageAliases(pack.name).map(name =>
    [`${name}@${pack.version}`, name === pack.name ? pack : {...pack, name}] as const)));
}
