import type { Node } from '@xyflow/react';
import type { CatalogPack, ParamSpec } from '../wire';
import { canonicalPackageName, samePackage } from '../packageIdentity';
import { declarationType } from '../types/legacyPortType';
import type { PortDeclaration } from '../types/portTypeIR';
import { algorithmNodeData } from './nodeMeta';

const stable = (value: unknown): string => JSON.stringify(value, (_key, item) =>
  item && typeof item === 'object' && !Array.isArray(item)
    ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]])) : item);

/** Exact declaration equality, not the asymmetric "can connect" relation. */
export function samePorts(a: CatalogPack, b: CatalogPack): boolean {
  const ports = (pack: CatalogPack) => [pack.inputs, pack.outputs].map((group, groupIndex) =>
    Object.fromEntries(Object.entries(group).map(([name, spec]) => {
      return [name, {
        tags: [...new Set(spec.tags)].sort(),
        arrayed: !!spec.arrayed, scalar: !!spec.scalar, dim_labels: spec.dim_labels ?? [],
        // Axis-name matching is an input constraint. The resolved IR carries
        // its default on outputs too, where it is not a manifest contract.
        require_axis_label_match: groupIndex === 0 ? spec.require_axis_label_match ?? null : null,
        tags_from: spec.tags_from ?? null, dim_labels_from: spec.dim_labels_from ?? null,
        dim_labels_from_input: spec.dim_labels_from_input ?? null,
        dim_labels_drop_outer: spec.dim_labels_drop_outer ?? null,
        content_dims_from: spec.content_dims_from ?? null, any_of: spec.any_of ?? [],
        ir: declarationType(spec),
      }];
    })));
  return !!a.arrayable === !!b.arrayable && stable(ports(a)) === stable(ports(b));
}

type GraphPortTypes = { schemaVersion: 1; inputs: Record<string, { declaration: PortDeclaration }>; outputs: Record<string, { declaration: PortDeclaration }> };

function graphPortTypes(value: unknown): GraphPortTypes | null {
  if (!value || typeof value !== 'object') return null;
  const candidate = value as Partial<GraphPortTypes>;
  if (candidate.schemaVersion !== 1 || !candidate.inputs || !candidate.outputs) return null;
  const complete = (ports: Record<string, { declaration: PortDeclaration }>) => Object.values(ports).every(port =>
    Array.isArray(port?.declaration?.tags) && typeof port.declaration.arrayed === 'boolean' && typeof port.declaration.scalar === 'boolean');
  return complete(candidate.inputs) && complete(candidate.outputs) ? candidate as GraphPortTypes : null;
}

/** Converts complete graph declarations into the existing strict type-IR baseline. */
export function replacementBaselineFromGraph(current: CatalogPack, portTypes: unknown, arrayedToggle?: boolean, arrayable?: boolean): CatalogPack | null {
  const types = graphPortTypes(portTypes);
  // A selected fan-out mode alone cannot prove the old pack's capability.
  if (!types || arrayedToggle || typeof arrayable !== 'boolean') return null;
  const inputs = Object.fromEntries(Object.entries(types.inputs).map(([name, port]) => [name, {
    ...port.declaration, storage: 'dir', required: true, description: null,
  }])) as CatalogPack['inputs'];
  const outputs = Object.fromEntries(Object.entries(types.outputs).map(([name, port]) => [name, {
    ...port.declaration, storage: 'dir', preview: null, description: null,
  }])) as CatalogPack['outputs'];
  return {...current, arrayable, inputs, outputs};
}
function versionDescending(a: string, b: string): number {
  const parse = (value: string) => /^(\d+)\.(\d+)\.(\d+)(?:-([^+]+))?(?:\+.*)?$/.exec(value);
  const av = parse(a), bv = parse(b);
  if (!av || !bv) return b.localeCompare(a, undefined, {numeric: true});
  for (let i = 1; i <= 3; i++) if (+av[i] !== +bv[i]) return +bv[i] - +av[i];
  if (!av[4] || !bv[4]) return Number(!bv[4]) - Number(!av[4]);
  const ap = av[4].split('.'), bp = bv[4].split('.');
  for (let i = 0; i < Math.max(ap.length, bp.length); i++) {
    if (ap[i] === undefined) return 1;
    if (bp[i] === undefined) return -1;
    if (ap[i] === bp[i]) continue;
    const an = /^\d+$/.test(ap[i]), bn = /^\d+$/.test(bp[i]);
    if (an !== bn) return an ? 1 : -1;
    return bn ? +bp[i] - +ap[i] : bp[i].localeCompare(ap[i]);
  }
  return 0;
}
export function replacementCandidates(current: CatalogPack, catalog: CatalogPack[]): CatalogPack[] {
  return catalog.filter(pack => !pack.name.startsWith("@builtin/") && samePorts(current, pack)).sort((a, b) => {
    const an = canonicalPackageName(a.name), bn = canonicalPackageName(b.name), name = canonicalPackageName(current.name);
    return Number(bn === name) - Number(an === name) || an.localeCompare(bn) ||
      versionDescending(a.version, b.version);
  });
}
function compatibleSpec(a: ParamSpec, b: ParamSpec): boolean {
  return (a.type === b.type && (a.items ?? null) === (b.items ?? null)) || (a.type === 'int' && b.type === 'float');
}
function accepts(spec: ParamSpec, value: unknown): boolean {
  if (value == null) return spec.optional;
  if (spec.type === 'enum') return !!spec.values?.includes(value as string | number);
  if (spec.type === 'int' || spec.type === 'float') return typeof value === 'number' && Number.isFinite(value)
    && (spec.type !== 'int' || Number.isInteger(value)) && (spec.min == null || value >= spec.min) && (spec.max == null || value <= spec.max);
  if (spec.type === 'bool') return typeof value === 'boolean';
  if (spec.type === 'string' || spec.type === 'path') return typeof value === 'string';
  if (spec.type === 'array') return Array.isArray(value) && (!spec.items || value.every(item => accepts({...spec, type: spec.items!, items: null, optional: false}, item)));
  return false;
}
export function replaceAlgorithm(node: Node<Record<string, unknown>>, target: CatalogPack): Node<Record<string, unknown>> {
  const data = algorithmNodeData(node.data);
  const baseline = data?.replacementBaseline ?? data?.pack;
  if (!data || !baseline || !samePorts(baseline, target) || samePackage(data.pack.name, data.pack.version, target.name, target.version)) return node;
  const params: Record<string, unknown> = {}, bindings: Record<string, string> = {};
  const retained = new Set<string>();
  for (const [name, spec] of Object.entries(target.params)) {
    const old = data.pack.params[name];
    const value = Object.prototype.hasOwnProperty.call(data.params ?? {}, name) ? data.params![name] : old?.default;
    if (old && compatibleSpec(old, spec) && accepts(spec, value)) {
      params[name] = structuredClone(value); retained.add(name);
    } else if (spec.default !== undefined) params[name] = structuredClone(spec.default);
    // Bound values are validated against workflow variable declarations by the existing editor.
    if (old && stable({...old, default: null, description: null, pinned: false}) === stable({...spec, default: null, description: null, pinned: false}) && data.param_bindings?.[name]) {
      bindings[name] = data.param_bindings[name]; retained.add(name);
    }
  }
  return {...node, data: {...data, pack: target, replacementBaseline: target, replacementBasis: undefined, params, param_bindings: bindings,
    local_params: data.local_params?.filter(name => retained.has(name)),
  }};
}
