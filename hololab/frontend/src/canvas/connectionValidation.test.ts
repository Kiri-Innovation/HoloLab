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
  expect(validateConnection(conn, context({ tags: ['colmap-folder'], arrayed: true, dim_labels: ['frame'] }, union)).ok).toBe(true);
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
  expect(validateConnection(conn, ctx, new Set(['t'])).reason).toBe('这两个端口已经连接。');
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

it('allows replacing an occupied input only with a compatible source', () => {
  const ctx = context({tags:['image']}, {tags:['image']});
  ctx.edges = [{id:'old',source:'old-source',sourceHandle:'out',target:'t',targetHandle:'in'}];
  expect(validateConnection(conn, ctx).ok).toBe(true);
  expect(validateConnection(conn, ctx, new Set(['t'])).ok).toBe(true);
  ctx.catalogByKey.get('source@1')!.outputs.out.tags = ['mesh'];
  expect(validateConnection(conn, ctx).ok).toBe(false);
  expect(ctx.edges[0].id).toBe('old');
});

import compatibility from './arrayViewCompatibility.json';
function arrayViewContext() {
  const ctx = context({tags:['colmap4d'],arrayed:false}, {tags:['any'],arrayed:true});
  const scalar = {tags:['colmap4d'],arrayed:false,dim_labels:[]};
  const array = {tags:['colmap4d'],arrayed:true,dim_labels:['frame']};
  const providers = [
    {...compatibility, inputs:{model:scalar},outputs:{frames:array}},
    {name:'col4d/colmap4d-from-array',version:'0.1.0',inputs:{frames:array},outputs:{model:scalar}},
  ] as unknown as CatalogPack[];
  for (const p of providers) ctx.catalogByKey.set(`${p.name}@${p.version}`, p);
  return ctx;
}
it('admits a declared one-dimensional array view without mutating its scalar source', () => {
  const ctx=arrayViewContext();
  expect(validateConnection(conn,ctx).ok).toBe(true);
  expect(ctx.catalogByKey.get('source@1')!.outputs.out.arrayed).toBe(false);
  expect(ctx.edges).toEqual([]);
});
it('supports the exact old-gateway manifest fallback, but never a changed manifest', () => {
  const ctx=arrayViewContext();
  const owner=ctx.catalogByKey.get(`${compatibility.name}@${compatibility.version}`)! as CatalogPack & {type_capabilities?:unknown};
  delete owner.type_capabilities;
  expect(validateConnection(conn,ctx).ok).toBe(true);
  owner.manifest_hash='changed';
  expect(validateConnection(conn,ctx).ok).toBe(false);
  Object.assign(owner,{type_capabilities:compatibility.type_capabilities});
  expect(validateConnection(conn,ctx).ok).toBe(true);
  Object.assign(owner,{type_capabilities:{}});
  expect(validateConnection(conn,ctx).ok).toBe(false);
});
it('rejects missing or incompatible providers, wrong node, wrong axis and two-dimensional targets', () => {
  for (const change of ['missing','signature','node','axis','rank','tag']) {
    const ctx=arrayViewContext(), target=ctx.catalogByKey.get('target@1')!;
    const restore=ctx.catalogByKey.get('col4d/colmap4d-from-array@0.1.0')!;
    if(change==='missing') ctx.catalogByKey.delete('col4d/colmap4d-from-array@0.1.0');
    if(change==='signature') restore.outputs.model.tags=['image'];
    if(change==='node') ctx.nodes[1].assigned_node_id='absent';
    if(change==='axis') target.inputs.in.dim_labels=['cam'];
    if(change==='rank') target.inputs.in.dim_labels=['frame','cam'];
    if(change==='tag') target.inputs.in.tags=['image'];
    expect(validateConnection(conn,ctx).ok,change).toBe(false);
  }
});
it('rejects competing declarations rather than selecting catalog order', () => {
  const ctx=arrayViewContext();
  ctx.catalogByKey.set('other@1',{...ctx.catalogByKey.get(`${compatibility.name}@${compatibility.version}`)!,name:'other'});
  expect(validateConnection(conn,ctx).ok).toBe(false);
});
it('uses the same view for a scalar invocation lifted by fan-out', () => {
  const ctx=arrayViewContext();ctx.nodes[1].arrayed_toggle=true;
  ctx.catalogByKey.get('target@1')!.inputs.in={tags:['colmap4d'],arrayed:false} as CatalogPack['inputs'][string];
  expect(validateConnection(conn,ctx).ok).toBe(true);
});


it('broadcasts a complete source frame array while mapping camera/frame renders', () => {
  const frames = { tags: ['image'], arrayed: true, dim_labels: ['frame'] };
  const source = { ...frames, scalar: true };
  expect(validateConnection(conn, context(frames, source, true)).ok).toBe(true);
  expect(validateConnection(conn, context(frames, frames, true)).ok).toBe(false);
  expect(validateConnection(conn, context({ ...frames, dim_labels: ['camera', 'frame'] }, frames, true)).ok).toBe(true);
  expect(validateConnection(conn, context({ ...frames, dim_labels: ['camera', 'frame'] }, source, true)).ok).toBe(false);
});
it('maps uniform scalar union components over cameras without weakening their tags', () => {
  const union = { tags: [], any_of: [
    { tags: ['colmap4d-images'] }, { tags: ['colmap4d-timestamps'] },
  ] };
  for (const tag of ['colmap4d-images', 'colmap4d-timestamps']) {
    expect(validateConnection(conn, context({ tags: [tag], arrayed: true, dim_labels: ['camera'] }, union, true)).ok).toBe(true);
    expect(validateConnection(conn, context({ tags: [tag] }, union, true)).ok).toBe(false);
  }
  expect(validateConnection(conn, context({ tags: ['image'], arrayed: true }, union, true)).ok).toBe(false);
});
