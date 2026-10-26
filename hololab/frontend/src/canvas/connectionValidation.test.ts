import { describe, expect, it } from 'vitest';
import type { CatalogPack, GraphNode } from '../wire';
import { portsCompatible, effectivePortArrayed, effectivePortDimLabels } from '../tags';
import { validateConnection } from './connectionValidation';
import type { EdgeTypeCtx } from './edgeLabels';
import realCase from '../../e2e/fixtures/connection-real-case.json';

const conn = { source: 's', sourceHandle: 'out', target: 't', targetHandle: 'in' };
function context(src: object, input: object, toggle = false): EdgeTypeCtx {
  const packs = [
    { name: 'source', version: '1', inputs: {}, outputs: { out: src } },
    { name: 'target', version: '1', inputs: { in: input }, outputs: {}, arrayable: true },
  ] as unknown as CatalogPack[];
  const nodes = packs.map((p, i) => ({ id: i ? 't' : 's', algorithm_name: p.name,
    algorithm_version: p.version, params: {}, arrayed_toggle: i ? toggle : false })) as GraphNode[];
  return { nodes, edges: [], catalogByKey: new Map(packs.map(p => [`${p.name}@1`, p])) };
}

describe('feedback uses the final port compatibility predicate', () => {
  for (const srcArray of [false, true]) for (const tgtArray of [false, true])
    for (const scalar of [false, true]) for (const toggle of [false, true])
      for (const tag of ['image', 'colmap-folder', 'any', 'image_sequence']) {
        it(`${srcArray}/${tgtArray}/${scalar}/${toggle}/${tag}`, () => {
          const ctx = context({ tags: [tag], arrayed: srcArray, dim_labels: srcArray ? ['group'] : [] },
            { tags: ['image'], arrayed: tgtArray, dim_labels: ['frame'], scalar }, toggle);
          const expected = portsCompatible([tag], srcArray, ['image'],
            effectivePortArrayed(tgtArray, true, toggle, scalar), scalar, srcArray ? ['group'] : [],
            effectivePortDimLabels(tgtArray, ['frame'], true, toggle, scalar));
          expect(validateConnection(conn, ctx).ok).toBe(expected);
        });
      }
});
it('allows the actual merge_g0 → STG connection with the declared opt-out', () => {
  const packs = realCase.catalog as unknown as CatalogPack[];
  const ctx = { ...realCase.graph, nodes: realCase.graph.nodes as GraphNode[], catalogByKey: new Map(packs.map(p => [`${p.name}@${p.version}`, p])) };
  expect(validateConnection({ source: 'merge_g0', sourceHandle: 'folder', target: 'nmv4xtft71', targetHandle: 'colmap_frames' }, ctx)).toEqual({
    ok: true, reason: undefined,
    expected: 'arrayed[frame]<colmap-folder>', actual: 'arrayed[group]<colmap-folder>',
  });
});
it('accepts either shape of an any_of input and rejects fan-out ambiguity', () => {
  const union = { tags: [], any_of: [
    { tags: ['colmap4d'], arrayed: false },
    { tags: ['colmap-folder'], arrayed: true, dim_labels: ['frame'] },
  ] };
  expect(validateConnection(conn, context({ tags: ['colmap4d'] }, union)).ok).toBe(true);
  expect(validateConnection(conn, context({ tags: ['colmap-folder'], arrayed: true, dim_labels: ['group'] }, union)).ok).toBe(true);
  expect(validateConnection(conn, context({ tags: ['colmap-folder'] }, union)).ok).toBe(false);
  expect(validateConnection(conn, context({ tags: ['colmap4d'] }, union, true)).reason).toContain('fan-out');
});
it('explains scalar/array and tag mismatches separately', () => {
  expect(validateConnection(conn, context({ tags: ['image'], arrayed: true }, { tags: ['image'] })).reason).toContain('单个数据与数组');
  expect(validateConnection(conn, context({ tags: ['image'] }, { tags: ['mesh'] })).reason).toBe('数据类型不匹配。');
});
it('handles unavailable ports and view cards using the same decision', () => {
  const ctx = context({ tags: ['image'] }, { tags: ['image'] });
  expect(validateConnection({ ...conn, sourceHandle: 'absent' }, ctx).reason).toContain('端口声明不可用');
  expect(validateConnection(conn, ctx, new Set(['t'])).ok).toBe(true);
  ctx.edges = [{ id: 'e', ...conn }];
  expect(validateConnection(conn, ctx).reason).toBe('这两个端口已经连接。');
  expect(validateConnection(conn, ctx, new Set(['t'])).reason).toContain('已有输入');
  expect(validateConnection(conn, ctx, new Set(['s'])).reason).toContain('不能作为数据输出');
});

for (const strict of [true, false]) for (const src of [[], [''], ['group'], ['group', 'cam']])
  for (const dst of [[], [''], ['frame'], ['frame', 'view']]) for (const tag of ['image', 'mesh']) {
    it(`axis policy ${strict}/${src}/${dst}/${tag}`, () => {
      const expected = tag === 'image' && src.length === dst.length
        && (!strict || src.every((a, i) => !a || !dst[i] || a === dst[i]));
      const ctx = context({ tags: ['image'], arrayed: !!src.length, dim_labels: src },
        { tags: [tag], arrayed: !!dst.length, dim_labels: dst, require_axis_label_match: strict });
      expect(validateConnection(conn, ctx).ok).toBe(expected);
    });
  }
it('distinguishes the rule that rejects a connection', () => {
  const source = { tags: ['image'], arrayed: true, dim_labels: ['group', 'cam'] };
  const target = { tags: ['image'], arrayed: true, dim_labels: ['frame', 'view'] };
  expect(validateConnection(conn, context(source, target)).reason).toBe('数组轴名称不匹配。');
  expect(validateConnection(conn, context(source, { ...target, dim_labels: ['frame'], require_axis_label_match: false })).reason)
    .toBe('数组维度层数不匹配。');
});
