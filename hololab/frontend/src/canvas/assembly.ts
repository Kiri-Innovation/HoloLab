import type { CatalogPack, GraphNode } from '../wire';
export const ASSEMBLE = '@builtin/array-assemble';
export type AssemblyConfig = { slots: string[]; type_anchor_slot: string | null; axis_label: string };
export const newAssembly = (): AssemblyConfig => ({ slots: ['slot-0', 'slot-1'], type_anchor_slot: null, axis_label: 'item' });
// A presentation adapter only: no manifest, pack registration or pack execution.
export function assemblyPresentation(nodeIds: string[] = []): CatalogPack {
  return {name: ASSEMBLE, version: '1', manifest_hash: 'builtin-v1', node_ids: nodeIds,
    description: 'Assemble ordered inputs of exactly the same type into an array.', category: ['system'],
    docs: 'One or more connected inputs. Slot order determines element order. Nested arrays are not flattened.',
    source_entry: null, inputs: {}, outputs: {out: {tags: ['any'], storage: 'dir', description: 'array<T>', preview: null, arrayed: true, dim_labels: ['item']}}, params: {}};
}
export function assemblyFields(assembly?: AssemblyConfig) {
  return assembly ? {kind: 'intrinsic' as const, operation: 'array-assemble' as const, assembly} : {};
}
export function isAssembly(node: Pick<GraphNode, 'algorithm_name'>) { return node.algorithm_name === ASSEMBLE; }
