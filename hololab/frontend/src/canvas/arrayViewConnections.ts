/** Connection eligibility only. Preparation owns conversion execution and provenance. */
import type { CatalogPack } from '../wire';
import { portsCompatible } from '../tags';
import type { EdgeType } from './edgeLabels';
import compatibility from './arrayViewCompatibility.json';

type Provider = { name: string; version: string; input: string; output: string };
type View = {
  version: 1;
  array: { tags: string[]; arrayed: true; dim_labels: string[]; storage: 'dir' };
  to_array: Provider; from_array: Provider;
};
type Capabilities = Record<string, { array_views: Record<string, View> }>;
type CapablePack = CatalogPack & { type_capabilities?: Capabilities };
type Target = { tags: string[]; arrayed: boolean; dimLabels: string[]; strict: boolean };

function declarations(pack: CapablePack): Capabilities {
  if (pack.type_capabilities !== undefined) return pack.type_capabilities ?? {};
  // Rolling upgrade: the old gateway drops the capability field. Only use the
  // audited declaration when its exact manifest is still installed. Never infer
  // a capability from a type name, directory contents or an arbitrary provider.
  if (pack.name === compatibility.name && pack.version === compatibility.version
      && pack.manifest_hash === compatibility.manifest_hash) {
    return compatibility.type_capabilities as Capabilities;
  }
  return {};
}

export function hasArrayViewConnection(source: EdgeType, targets: Target[],
  catalog: ReadonlyMap<string, CatalogPack>, assignedNode?: string | null): boolean {
  if (source.arrayed || !source.tags.length || source.tags.includes('any')) return false;
  const offers = new Map<string, { owner: string; view: View }>();
  for (const pack of catalog.values()) {
    for (const [tag, cap] of Object.entries(declarations(pack))) {
      if (!source.tags.includes(tag)) continue;
      for (const [name, view] of Object.entries(cap.array_views ?? {})) {
        const key = `${tag}/${name}`, owner = `${pack.name}@${pack.version}`;
        const previous = offers.get(key);
        if (previous && (previous.owner !== owner || JSON.stringify(previous.view) !== JSON.stringify(view))) return false;
        offers.set(key, { owner, view });
      }
    }
  }
  const candidates = [...offers.values()].filter(({ view }) => view.version === 1
    && view.array?.arrayed === true && view.array.storage === 'dir'
    && view.array.dim_labels?.length === 1 && !!view.array.dim_labels[0]
    && view.array.tags?.length && targets.some(target => target.arrayed
      && portsCompatible(view.array.tags, true, target.tags, true, false,
        view.array.dim_labels, target.dimLabels, target.strict)));
  if (candidates.length !== 1) return false;
  const view = candidates[0].view;
  const signature = (provider: Provider, toArray: boolean): boolean => {
    if (!provider) return false;
    const pack = catalog.get(`${provider.name}@${provider.version}`);
    if (!pack || (assignedNode && !pack.node_ids?.includes(assignedNode))) return false;
    const input = pack.inputs[provider.input], output = pack.outputs[provider.output];
    if (!input || !output || input.any_of?.length) return false;
    if (Object.entries(pack.inputs).some(([name, spec]) => name !== provider.input && spec.required !== false)) return false;
    const scalarTags = source.tags;
    return portsCompatible(input.tags, !!input.arrayed, toArray ? scalarTags : view.array.tags,
      !toArray, false, input.dim_labels ?? [], toArray ? [] : view.array.dim_labels)
      && portsCompatible(output.tags, !!output.arrayed, toArray ? view.array.tags : scalarTags,
        toArray, false, output.dim_labels ?? [], toArray ? view.array.dim_labels : []);
  };
  return signature(view.to_array, true) && signature(view.from_array, false);
}
