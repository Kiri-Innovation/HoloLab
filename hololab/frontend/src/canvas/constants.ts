import type { CatalogPack } from '../wire';
export const CONSTANT_PREFIX = '@builtin/constant-';
export const isConstantName = (name: string) => name === CONSTANT_PREFIX + 'float-array' || name === CONSTANT_PREFIX + 'vec3-array';
export function constantFields(name: string) {
  return isConstantName(name) ? {kind: 'intrinsic' as const, operation: name.slice('@builtin/'.length) as 'constant-float-array' | 'constant-vec3-array'} : {};
}
export function constantPresentation(vector: boolean): CatalogPack {
  const name = vector ? 'vec3-array' : 'float-array';
  const spec = {type: 'array', items: 'float', default: [], pinned: true, description: '', optional: false};
  return {name: CONSTANT_PREFIX + name, version: '1', manifest_hash: 'constant-v1', node_ids: [],
    category: ['constant'], description: 'Workflow constant. No job or device assignment.', docs: 'Values are stored in the workflow; input files are prepared only for consumers.', source_entry: null,
    inputs: {}, outputs: {[vector ? 'transforms' : 'offsets']: {tags: [vector ? 'vec3' : 'float'], storage: 'dir', description: 'constant array', preview: null, arrayed: true, dim_labels: ['layer']}},
    params: vector ? {x: {...spec}, y: {...spec}, scale: {...spec}} : {items: {...spec}}} as CatalogPack;
}
export function constantError(name: string, params: Record<string, unknown>): string | null {
  const keys = name.endsWith('vec3-array') ? ['x', 'y', 'scale'] : ['items'];
  const cols = keys.map(k => params[k] ?? []);
  if (cols.some(c => !Array.isArray(c))) return '参数必须是数组';
  const arrays = cols as unknown[][];
  if (!arrays[0].length) return '数组不能为空';
  if (new Set(arrays.map(c => c.length)).size !== 1) return 'x/y/scale 数量必须相同';
  for (let c = 0; c < keys.length; c++) for (let i = 0; i < arrays[c].length; i++) {
    const v = arrays[c][i];
    if (typeof v !== 'number' || !Number.isFinite(v)) return `${keys[c]}[${i}]: 必须是有限数字`;
  }
  return null;
}

export function constantSignature(name: string, params: Record<string, unknown>): string {
  if (constantError(name, params)) return 'invalid:' + JSON.stringify(params);
  const keys = name.endsWith('vec3-array') ? ['x', 'y', 'scale'] : ['items'];
  return JSON.stringify([1, name, ...keys.map(k => params[k])]);
}
