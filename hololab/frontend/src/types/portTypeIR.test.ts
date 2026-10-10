import { describe, expect, it } from 'vitest';
import { broadcastInput, invocationFromAggregate, objectType, unknownRuntime, unknownType,
  unwrapArrays, viewsFromInvocation, wrapArray, wrapArrays,
  type Observation, type Participation, type PortTypeIR, type TypeExpr } from './portTypeIR';

function freeze<T>(value: T): T {
  if (value && typeof value === 'object') {
    Object.values(value).forEach(freeze);
    Object.freeze(value);
  }
  return value;
}
const image = objectType(['image'], [{ label: 'cam' }]);
const cases: [string, TypeExpr][] = [
  ['scalar', objectType(['colmap-cams'])], ['content-axis', image],
  ['array', wrapArray(image, { label: 'frame' })],
  ['nested', wrapArrays(image, [{ label: 'batch' }, { label: 'frame' }])],
  ['unknown', unknownType('unconnected')], ['array-unknown', wrapArray(unknownType('unresolved-tag'))],
  ['wildcard', objectType(['any'])], ['multiple-tags', objectType(['image', 'rgb'])],
];
const modes: Participation[] = ['none', 'map-input', 'broadcast-input', 'collect-output'];
describe('structural type views', () => {
  for (const [name, type] of cases) for (const mode of modes) {
    it(`${name}: ${mode} preserves invocation and only adds the requested collection`, () => {
      const input = freeze(structuredClone(type));
      const before = JSON.stringify(input);
      const views = viewsFromInvocation(input, mode, { label: 'frame' });
      expect(views.invocation).toEqual(type);
      expect(views.participation).toBe(mode);
      if (mode === 'none' || mode === 'broadcast-input') expect(views.aggregate).toBe(input);
      else {
        expect(views.aggregate).toEqual({ kind: 'array', axis: { label: 'frame' }, element: type });
        expect(invocationFromAggregate(views.aggregate)).toEqual({ ok: true, type });
      }
      expect(JSON.stringify(input)).toBe(before);
      expect(JSON.parse(JSON.stringify(views))).toEqual(views);
    });
  }
  for (const depth of [0, 1, 2, 3, 8]) for (const [name, type] of cases) {
    it(`wrap/pop round trip: ${name}, ${depth} layers`, () => {
      const axes = Array.from({ length: depth }, (_, i) => ({ label: i % 2 ? null : `axis-${i}` }));
      const result = wrapArrays(type, axes);
      expect(unwrapArrays(result, depth)).toEqual({ ok: true, type });
      if (depth) expect(result).toMatchObject({ axis: axes[0] });
    });
  }
  it('does not pop scalar or content dimensions', () => {
    expect(unwrapArrays(image)).toEqual({ ok: false, reason: 'not-array', type: image });
    const nested = wrapArray(image);
    expect(unwrapArrays(nested, 2)).toEqual({ ok: false, reason: 'not-array', type: nested });
  });
  it('reports unresolved structure without fabricating a scalar', () => {
    const unknown = unknownType('missing-source');
    expect(unwrapArrays(unknown)).toEqual({ ok: false, reason: 'unknown-type', type: unknown });
    expect(unwrapArrays(wrapArray(unknown))).toEqual({ ok: true, type: unknown });
  });
  for (const depth of [-1, 0.5, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1]) {
    it(`rejects invalid depth ${depth}`, () => expect(unwrapArrays(image, depth)).toEqual({ ok: false, reason: 'invalid-depth', type: image }));
  }
  it('broadcasts a complete nested array without adding or removing axes', () => {
    const type = wrapArrays(image, [{ label: 'frame' }, { label: 'cam' }]);
    expect(broadcastInput(type)).toEqual({ participation: 'broadcast-input', invocation: type, aggregate: type });
  });
  it('keeps tag names opaque and separates wildcard from unknown', () => {
    expect(objectType(['arrayed<frame> of custom'])).toMatchObject({ kind: 'object', tags: ['arrayed<frame> of custom'] });
    expect(objectType(['any']).kind).toBe('object');
    expect(objectType([]).kind).toBe('unknown');
  });
  it('copies caller-owned tags and axis metadata', () => {
    const tags = ['image']; const axes = [{ label: 'cam' }];
    const type = objectType(tags, axes); const wrapped = wrapArray(type, axes[0]);
    tags.push('rgb'); axes[0].label = 'changed';
    expect(type).toEqual(image);
    expect(wrapped).toMatchObject({ axis: { label: 'cam' } });
  });
});

describe('declaration and measurement remain separate', () => {
  for (const status of ['unknown', 'sampled', 'complete'] as const) for (const value of [0, 21, 100]) {
    it(`${status} observation (${value}) survives JSON without becoming a declaration`, () => {
      const count: Observation<number> = status === 'unknown'
        ? { evidence: { status, reason: 'legacy-coverage-unrecorded' }, legacyValue: value }
        : { evidence: status === 'sampled' ? { status, value, source: 'tag-probe', sample: 'frame_0000' }
          : { status, value, source: 'full-scan' } };
      const ir: PortTypeIR = { schemaVersion: 1,
        declaration: { tags: ['colmap-cams'], scalar: true, dim_labels: [] },
        views: broadcastInput(objectType(['colmap-cams'])),
        runtime: { ...unknownRuntime('handle-1', 'not-measured'), internalCounts: [{ key: 'pose', count }] } };
      expect(JSON.parse(JSON.stringify(ir))).toEqual(ir);
      expect(ir.views.aggregate).toEqual(objectType(['colmap-cams']));
      expect(ir.runtime.internalCounts[0].count.evidence.status).toBe(status);
    });
  }
  it('distinguishes unavailable shape from measured scalar and preserves zero', () => {
    const missing = unknownRuntime(null, 'not-run');
    expect(missing.axes.evidence.status).toBe('unknown');
    const scalar = { evidence: { status: 'complete', value: [], source: 'handle-probe' } } as const;
    expect(scalar).not.toEqual(missing.axes);
    const emptyArray: Observation<number> = { evidence: { status: 'complete', value: 0, source: 'directory-scan' } };
    expect(emptyArray.evidence).toHaveProperty('value', 0);
  });
});
