import { NodeInspector } from './NodeInspector';
import { expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import {
  InlineInputEditor,
  inlineInputSource,
  toggledInlineInputEditor,
} from './InlineInputEditor';
import {
  inputValueProblems, freshInlineValue, reconcileInlineValues,
  inlineIdentity,
  inlineError,
} from './inlineValues';
import { computeStaleness } from './staleness';
import type { CatalogPack, GraphNode, InputPortSpec } from '../wire';
vi.mock('./OpenSourceButton', () => ({ OpenSourceButton: () => null }));
const spec = {
  tags: ['float'],
  storage: 'file',
  required: true,
  arrayed: false,
} as InputPortSpec;
const node: GraphNode = {
  id: 'n',
  algorithm_name: 'consume',
  algorithm_version: '1',
  position: { x: 0, y: 0 },
  params: {},
  port_values: { value: 0 },
  assigned_node_id: 'device',
};
const wire = {
  id: 'e',
  source: 'source',
  sourceHandle: 'out',
  target: 'n',
  targetHandle: 'value',
};
it('resolves wire, literal, default, required missing and explicit empty', () => {
  expect(inputValueProblems({ value: spec }, {}, [])).toEqual(['value']);
  expect(inputValueProblems({ value: spec }, { value: 0 }, [])).toEqual([]);
  expect(
    inputValueProblems({ value: spec }, { value: 'invalid' }, ['value']),
  ).toEqual([]);
  expect(
    inputValueProblems(
      { value: { ...spec, inline_default: 2 } },
      { value: null },
      [],
    ),
  ).toEqual([]);
  expect(
    inputValueProblems(
      { value: { ...spec, arrayed: true, storage: 'dir' } },
      { value: [] },
      [],
    ),
  ).toEqual([]);
  expect(
    inputValueProblems({ value: { ...spec, required: false } }, {}, []),
  ).toEqual([]);
});
it.each([NaN, Infinity, true, '2'])('rejects malformed scalar %s', (v) =>
  expect(inlineError(spec, v)).toBeTruthy(),
);
it('limits literals to value types and three-component vectors', () => {
  expect(inlineError({ ...spec, tags: ['image'] }, 'path')).toBeTruthy();
  expect(inlineError({ ...spec, tags: ['vec3'] }, [0, 1])).toBeTruthy();
  expect(inlineError({ ...spec, tags: ['vec3'] }, [0, -1, 0])).toBeNull();
  expect(inlineError({ ...spec, inline: false }, 0)).toBeTruthy();
});
it('ignores inactive backup changes but propagates active value drift', () => {
  const consumer = { ...node, id: 'consumer', port_values: {} };
  const downstream = {
    id: 'next',
    source: 'n',
    sourceHandle: 'out',
    target: 'consumer',
    targetHandle: 'value',
  };
  const snap = { nodes: [node, consumer], edges: [downstream] };
  const draft = {
    nodes: [{ ...node, port_values: { value: -1 } }, consumer],
    edges: [downstream],
  };
  const jobs = { n: { state: 'done' }, consumer: { state: 'done' } };
  expect(computeStaleness(draft, snap, jobs).n?.kind).toBe('self_dirty');
  expect(computeStaleness(draft, snap, jobs).consumer?.kind).toBe(
    'upstream_dirty',
  );
  expect(inlineIdentity(node, [wire])).toBe(
    inlineIdentity(draft.nodes[0], [wire]),
  );
  const source = { ...node, id: 'source', port_values: {} };
  const wiredSnap = { nodes: [source, node], edges: [wire] };
  const wiredDraft = { nodes: [source, draft.nodes[0]], edges: [wire] };
  expect(
    computeStaleness(wiredDraft, wiredSnap, {
      source: { state: 'done' },
      n: { state: 'done' },
    }).n,
  ).toBeNull();
  expect(computeStaleness(draft, snap, jobs)).toEqual(
    computeStaleness(draft, snap, jobs),
  );
});
it('compares current default against frozen snapshot value', () => {
  const draft = { nodes: [{ ...node, port_values: {} }], edges: [] };
  const snap = { nodes: [node], edges: [] };
  expect(
    computeStaleness(
      draft,
      snap,
      { n: { state: 'done' } },
      [],
      () => ({}),
      () => ({ value: 0 }),
    ).n,
  ).toBeNull();
  expect(
    computeStaleness(
      draft,
      snap,
      { n: { state: 'done' } },
      [],
      () => ({}),
      () => ({ value: 2 }),
    ).n?.kind,
  ).toBe('self_dirty');
});
it('renders source icon and keeps reusable scalar/array editors collapsed', () => {
  const pack = {
    inputs: { value: { ...spec, inline_default: 2 } },
    arrayable: false,
  } as unknown as CatalogPack;
  const html = renderToStaticMarkup(
    <InlineInputEditor
      node={{ ...node, port_values: {} }}
      pack={pack}
      onChange={() => {}}
    />,
  );
  expect(html).toContain('端口默认值');
  expect(html).toContain('aria-expanded="false"');
  expect(html).not.toContain('value="2"');
  const wired = renderToStaticMarkup(
    <InlineInputEditor
      node={node}
      pack={pack}
      sources={{ value: 'float-array' }}
      onChange={() => {}}
    />,
  );
  expect(wired).toBe('');
  expect(node.port_values).toEqual({ value: 0 });
  const vector = renderToStaticMarkup(
    <InlineInputEditor
      node={{
        ...node,
        port_values: {
          value: [
            [0, 0, 1],
            [0.727, 0.0198, 0.25],
          ],
        },
      }}
      pack={{
        ...pack,
        inputs: {
          value: { ...spec, tags: ['vec3'], storage: 'dir', arrayed: true },
        },
      }}
      onChange={() => {}}
    />,
  );
  expect(vector).toContain('aria-label="value：内联值；点击展开编辑控件"');
  expect(vector).not.toContain('transform array');
  expect(vector).not.toContain('Add transform item');
});

it('labels each inline input source and toggles its collapsed editor state', () => {
  const editable = { ...spec, inline: true };
  expect(inlineInputSource({ value: 1 }, 'value', editable, true)).toBe('inline');
  expect(inlineInputSource({}, 'value', { ...editable, inline_default: 1 }, true)).toBe('port-default');
  expect(inlineInputSource({}, 'value', editable, true)).toBe('type-default');
  expect(inlineInputSource({}, 'value', { ...editable, inline: false }, false)).toBe('unset');
  expect(toggledInlineInputEditor(false)).toBe(true);
  expect(toggledInlineInputEditor(true)).toBe(false);

  const html = renderToStaticMarkup(
    <InlineInputEditor
      node={{ ...node, port_values: { inline: 1 } }}
      pack={{
        inputs: {
          inline: editable,
          portDefault: { ...editable, inline_default: 2 },
          typeDefault: editable,
          unset: { ...editable, tags: ['image'], inline: false },
        },
        arrayable: false,
      } as unknown as CatalogPack}
      onChange={() => {}}
    />,
  );
  expect(html).toContain('aria-label="inline：内联值；点击展开编辑控件"');
  expect(html).toContain('aria-label="portDefault：端口默认值；点击展开编辑控件"');
  expect(html).toContain('aria-label="typeDefault：类型默认值；点击展开编辑控件"');
  expect(html).toContain('aria-label="unset：未设置；请连接上游"');
  expect(html).toContain('请连接上游（必填）');
});

it('omits the entire input section when wired or portless, retains unset optional inputs', () => {
  const pack = {name:'consume',version:'1',inputs:{value:{...spec,required:false}},outputs:{},params:{},description:''} as unknown as CatalogPack;
  const render = (inputs: CatalogPack['inputs'], sources: Record<string,string> = {}) => renderToStaticMarkup(
    <NodeInspector selected={{...node,port_values:{}}} pack={{...pack,inputs}} inputSources={sources} onChange={()=>{}} onParamsChange={()=>{}} assignment={<span>Device</span>} />
  );
  const optional = render(pack.inputs);
  expect(optional).toContain('aria-label="Input values"');
  expect(optional).toContain('aria-label="value：类型默认值；点击展开编辑控件"');
  expect(optional).not.toContain('value="0"');
  expect(optional).not.toContain('设置内联值');
  expect(optional).not.toContain('清除内联值');
  const wired = render(pack.inputs, {value:'upstream'});
  expect(wired).not.toContain('hl-inspector-inputs');
  expect(wired).toContain('hl-inspector-execution');
  expect(wired).toContain('aria-label="Parameters"');
  expect(render({})).not.toContain('hl-inspector-inputs');
  expect(render({value:spec})).toContain('aria-label="value：类型默认值；点击展开编辑控件"');
});

it('creates fresh format defaults and discards connected values across all edge replacements', () => {
  expect(freshInlineValue(spec)).toBe(0);
  expect(freshInlineValue({...spec,tags:['vec3']})).toEqual([0,0,0]);
  expect(freshInlineValue({...spec,arrayed:true})).toEqual([]);
  expect(freshInlineValue({...spec,tags:['vec3'],arrayed:true})).toEqual([]);
  expect(freshInlineValue({...spec,tags:['image']})).toBeUndefined();
  expect(freshInlineValue(spec,true)).toEqual([]);
  const defaults={...spec,tags:['vec3'],inline_default:[1,2,3]};
  const copy=freshInlineValue(defaults) as number[];copy[0]=9;
  expect(defaults.inline_default).toEqual([1,2,3]);
  const inputs={a:spec,b:{...spec,required:false}};
  const old={a:.727,b:4};
  const wired=reconcileInlineValues(inputs,old,['a','b']);
  expect(wired).toEqual({});expect(old).toEqual({a:.727,b:4});
  const disconnected=reconcileInlineValues(inputs,wired,[]);
  expect(disconnected).toEqual({a:0,b:0});
  expect(reconcileInlineValues(inputs,disconnected,[])).toBe(disconnected);
  // A replacement/replayed edge set is reconciled identically, not from undo values.
  expect(reconcileInlineValues(inputs,disconnected,['a'])).toEqual({b:0});
});
