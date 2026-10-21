import { describe, expect, it } from 'vitest';
import { constantError, constantFields, constantPresentation, constantSignature } from './constants';
import { computeStaleness } from './staleness';

 describe('workflow constants', () => {
  it('has typed ports, native arrays and default pins without devices', () => {
    const p = constantPresentation(true);
    expect(p.node_ids).toEqual([]);
    expect(p.inputs).toEqual({});
    expect(p.outputs.transforms.tags).toEqual(['vec3']);
    expect(p.params.x.pinned).toBe(true);
    expect(constantFields(p.name).operation).toBe('constant-vec3-array');
  });
  it('validates and preserves ordered value identity', () => {
    const name = constantPresentation(false).name;
    expect(constantError(name, {items: [0, 2.5]})).toBeNull();
    expect(constantError(name, {items: [NaN]})).not.toBeNull();
    expect(constantSignature(name, {items: [0, 2]})).not.toBe(constantSignature(name, {items: [2, 0]}));
  });
});

it('constant changes dirty consumers but never require running the constant', () => {
  const constant = {id: 'c', ...constantFields(constantPresentation(false).name), algorithm_name: constantPresentation(false).name, algorithm_version:'1', position:{x:0,y:0}, params:{items:[0]}, assigned_node_id:null};
  const task = {id:'t', algorithm_name:'task', algorithm_version:'1',position:{x:0,y:0},params:{},assigned_node_id:'device'};
  const edges = [{id:'e',source:'c',sourceHandle:'offsets',target:'t',targetHandle:'offsets'}];
  const snap = {nodes:[constant,task],edges};
  const draft = {nodes:[{...constant,params:{items:[2]}},task],edges};
  const result = computeStaleness(draft,snap,{t:{state:'done',job_id:'job'}});
  expect(result.c).toBeNull();
  expect(result.t?.kind).toBe('upstream_dirty');
});


describe('constant format validation has no algorithm ranges', () => {
  it('accepts negative floats and nonpositive vector components', () => {
    const scalar = constantPresentation(false);
    const vector = constantPresentation(true);
    expect(constantError(scalar.name, {items: [0, -1]})).toBeNull();
    expect(constantError(vector.name, {x: [-1, 0], y: [0, -2], scale: [0, -1]})).toBeNull();
    expect(constantSignature(scalar.name, {items: [-1]})).not.toMatch(/^invalid:/);
    for (const pack of [scalar, vector]) for (const spec of Object.values(pack.params)) {
      expect(spec.min).toBeUndefined();
      expect(spec.max).toBeUndefined();
    }
  });
  it.each([NaN, Infinity, -Infinity, true, '2'])('rejects malformed number %s', value => {
    expect(constantError(constantPresentation(false).name, {items: [value]})).toContain('有限数字');
    expect(constantError(constantPresentation(true).name, {x: [0], y: [0], scale: [value]})).toContain('有限数字');
  });
  it('rejects empty arrays and incomplete vectors', () => {
    expect(constantError(constantPresentation(false).name, {items: []})).toBe('数组不能为空');
    expect(constantError(constantPresentation(true).name, {x: [0], y: [0], scale: []})).toContain('数量必须相同');
  });
});
