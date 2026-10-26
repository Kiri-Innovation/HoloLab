import {canonicalPackageName} from '../packageIdentity';

/** Build once from the whole view's catalog, before searching or filtering. */
export function packDisplayNames(names: readonly string[]): (name: string) => string {
  const identities = new Map<string, Set<string>>();
  for (const name of names) {
    const identity = canonicalPackageName(name);
    const short = identity.slice(identity.lastIndexOf('/') + 1);
    const group = identities.get(short) ?? new Set<string>();
    group.add(identity);
    identities.set(short, group);
  }
  return name => {
    const identity = canonicalPackageName(name);
    const short = identity.slice(identity.lastIndexOf('/') + 1);
    return (identities.get(short)?.size ?? 0) > 1 ? identity : short;
  };
}
