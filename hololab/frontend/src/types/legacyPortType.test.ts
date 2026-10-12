import {measured} from '../test/measurements';
import { describe, expect, it } from 'vitest';
import { adaptPortType, declarationType, elementRuntime, legacyViews, runtimeFromSummary, typeFromLegacy } from './legacyPortType';
import { observedValue, renderPortType } from './renderPortType';
import { objectType, unknownType, wrapArray, type TypeExpr } from './portTypeIR';
import { effectiveOutputType, formatTypeLabel, formatTypeLabelLong } from '../canvas/edgeLabels';
import { typeTooltip } from '../canvas/typeTooltip';
import type { CatalogPack, GraphNode } from '../wire';

const tags = ['image', 'rgb'];
const scalar = { tags, arrayed: false, dimLabels: [] };
const contexts = [
  { fanout: false, direction: 'input' as const },
  { fanout: true, direction: 'input' as const },
  { fanout: true, direction: 'input' as const, broadcast: true },
  { fanout: true, direction: 'output' as const },
];
for (const labels of [[], [''], ['frame'], ['', 'cam'], ['batch', 'frame', 'cam']]) {
  for (const arrayed of [false, true]) for (const context of contexts) {
    it(`adapts ${JSON.stringify(labels)} arrayed=${arrayed} ${JSON.stringify(context)}`, () => {
      const fields = { ...scalar, arrayed, dimLabels: labels };
      const before = JSON.stringify(fields);
      const type = typeFromLegacy(fields);
      const aggregate = legacyViews(type, context, 'aggregate');
      expect(aggregate.aggregate).toEqual(type); // no duplicate wrapping, for every case
      const invocation = legacyViews(type, context, 'invocation');
      const broadcast = context.direction === 'input' && context.broadcast;
      if (context.fanout && !broadcast) expect(invocation.aggregate).toEqual(wrapArray(type));
      else expect(invocation.aggregate).toEqual(type);
      expect(JSON.stringify(fields)).toBe(before);
      expect(JSON.parse(JSON.stringify(aggregate))).toEqual(aggregate);
    });
  }
}

describe('terminal renderer and outlet compatibility facades', () => {
  const shapes: [TypeExpr, string][] = [
    [objectType(['image']), 'image'],
    [wrapArray(objectType(['image'])), 'arrayed<image>'],
    [wrapArray(objectType(['colmap-cams']), { label: 'frame' }), 'arrayed[frame]<colmap-cams>'],
    [wrapArray(wrapArray(objectType(['image']), { label: 'cam' }), { label: 'frame' }), 'arrayed[frame]<arrayed[cam]<image>>'],
    [unknownType('missing'), 'unknown'],
    [wrapArray(unknownType('missing')), 'arrayed<unknown>'],
    [objectType(['any']), 'any'],
  ];
  for (const [type, expected] of shapes) it(`renders declaration ${expected}`, () => {
    expect(renderPortType(type, { mode: 'declaration' })).toBe(expected);
    expect(renderPortType(type, { mode: 'long' })).toBe(expected);
  });
  for (const sizes of [[100], [100, 21], [2, 100, 21], [0]]) {
    it(`uses authoritative runtime axes ${sizes}`, () => {
      const actual = { ...scalar, dimSizes: sizes, elementCount: 999, innerElementCount: 888, runtimeShape: measured(tags, [], sizes) };
      const runtime = runtimeFromSummary(actual);
      expect(runtime.axes.evidence.status).toBe('complete');
      expect(observedValue(runtime.axes)?.map(axis => observedValue(axis.size))).toEqual(sizes);
      const suffix = [...sizes].reverse().map(n => `[${n}]`).join('');
      expect(renderPortType(typeFromLegacy(scalar), { mode: 'runtime', runtime })).toBe(`image${suffix}, rgb${suffix}`);
      expect(formatTypeLabel(actual)).toBe(`image+1${suffix}`);
    });
  }
  it('all declaration outlets use the same labeled structural grammar', () => {
    const fields = { tags: ['colmap-cams'], arrayed: true, dimLabels: ['frame'] };
    const expected = 'arrayed[frame]<colmap-cams>';
    // Inspector, snapshot labelLong and front-end incompatibility labels share this facade.
    expect(formatTypeLabelLong(fields)).toBe(expected);
    const tooltip = typeTooltip('cams', fields, { ...fields, runtimeAvailable: false }, { fanout: true, direction: 'input' });
    expect(tooltip['data-tooltip-declared']).toBe(expected);
    expect(tooltip['data-tooltip-after-declared']).toBe(expected);
    expect(tooltip['data-tooltip-after-actual']).toBe('未知');
    expect(formatTypeLabel(fields)).toBe('colmap-cams[frame:?]');
  });
  it('does not promote scalar content axes to array wrappers', () => {
    const type = typeFromLegacy({ tags: ['image'], arrayed: false, dimLabels: ['cam'] });
    expect(type).toEqual(objectType(['image'], [{ label: 'cam' }]));
    expect(renderPortType(type, { mode: 'declaration' })).toBe('image[cam]');
  });
  it('preserves declared source independently of effective aggregate and measured tags', () => {
    const raw = { tags: ['any'], arrayed: false, scalar: false, tags_from: 'in' };
    const effective = { tags: ['image'], arrayed: true, dimLabels: ['frame'] };
    const actual = { ...effective, dimSizes: [100], runtimeAvailable: true };
    const ir = adaptPortType(raw, effective, actual, { fanout: true, direction: 'output' });
    expect(ir.declaration).toEqual(raw);
    expect(renderPortType(declarationType(raw), { mode: 'declaration' })).toBe('any');
    expect(ir.views.invocation).toEqual(objectType(['image']));
    expect(renderPortType(ir.views.aggregate, { mode: 'declaration' })).toBe('arrayed[frame]<image>');
    const tip = typeTooltip('out', effective, actual, { fanout: true, direction: 'output' }, raw);
    expect(tip['data-tooltip-declared']).toBe('any');
    expect(tip['data-tooltip-after-declared']).toBe('arrayed[frame]<image>');
  });
  it('trusts backend resolved_outputs, including historical graph context', () => {
    const node = { id: 'n', position: { x: 0, y: 0 }, assigned_node_id: null, algorithm_name: 'test', algorithm_version: '1', params: {},
      resolved_outputs: { out: { tags: ['image'], arrayed: true, dim_labels: ['old-frame'] } } } as GraphNode;
    const pack = { outputs: { out: { tags: ['any'], arrayed: false, dim_labels: [] } } } as unknown as CatalogPack;
    const fields = effectiveOutputType('n', 'out', { nodes: [node], edges: [], catalogByKey: new Map([['test@1', pack]]) });
    expect(formatTypeLabelLong(fields)).toBe('arrayed[old-frame]<image>');
    expect(legacyViews(typeFromLegacy(fields), { fanout: true, direction: 'output' }, 'aggregate').aggregate)
      .toEqual(wrapArray(objectType(['image']), { label: 'old-frame' }));
  });
  it('ignores old numbers without runtime_shape', () => {
    const actual = { tags: ['colmap-cams'], arrayed: true, dimLabels: ['frame'], dimSizes: [100],
      internalCountItems: [{ label: 'pose', value: 21 }] };
    const runtime = runtimeFromSummary(actual, 'h');
    expect(observedValue(runtime.axes)).toBeUndefined();
    expect(runtime.internalCounts).toEqual([]);
    expect(renderPortType(typeFromLegacy(actual), {mode:'runtime',runtime})).toBe('unknown');
    expect(formatTypeLabel(actual)).toBe('colmap-cams[frame:?]');
  });
  for (const status of ['unknown', 'sampled', 'complete'] as const) it(`preserves ${status} evidence`, () => {
    const runtime = measured(tags,['frame','cam'],[100,21]);
    const axes = observedValue(runtime.axes)!;
    const evidence = status === 'unknown' ? {evidence:{status,reason:'unmeasured'},legacyValue:axes}
      : {evidence:status === 'sampled' ? {status,value:axes,source:'probe',sample:'first'} : {status,value:axes,source:'scan'}};
    const projected=elementRuntime({...runtime,axes:evidence});
    expect(projected.axes.evidence.status).toBe(status);
    expect(observedValue(projected.axes)).toEqual(status==='unknown'?undefined:axes.slice(1));
  });
  it('unknown data never renders a declaration as a measured result', () => {
    const runtime = runtimeFromSummary({ ...scalar, runtimeAvailable: false });
    expect(renderPortType(typeFromLegacy(scalar), { mode: 'runtime', runtime, unknown: '未知' })).toBe('未知');
    const fields = { tags: ['image'], arrayed: true, dimLabels: ['frame'], runtimeAvailable: false };
    expect(formatTypeLabel(fields)).toBe('image[frame:?]');
    expect(formatTypeLabel({ ...fields, tags: [] })).toBe('?[frame:?]');
    expect(typeTooltip('images', fields, fields)['data-tooltip-actual']).toBe('未知');
  });
});
