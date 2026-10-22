import { NodeInspector } from './NodeInspector';
import { expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { InlineInputEditor } from './InlineInputEditor';
import {
  inputValueProblems,
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
it('renders source and visible default with reusable scalar/array editors', () => {
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
  expect(html).toContain('value="2"');
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
  expect(vector).toContain('transform array');
  expect(vector).toContain('0.727');
  expect(vector).toContain('Add transform item');
});

it('omits the entire input section when wired or portless, retains unset optional inputs', () => {
  const pack = {name:'consume',version:'1',inputs:{value:{...spec,required:false}},outputs:{},params:{},description:''} as unknown as CatalogPack;
  const render = (inputs: CatalogPack['inputs'], sources: Record<string,string> = {}) => renderToStaticMarkup(
    <NodeInspector selected={{...node,port_values:{}}} pack={{...pack,inputs}} inputSources={sources} onChange={()=>{}} onParamsChange={()=>{}} assignment={<span>Device</span>} />
  );
  const optional = render(pack.inputs);
  expect(optional).toContain('aria-label="Input values"');
  expect(optional).toContain('设置内联值');
  const wired = render(pack.inputs, {value:'upstream'});
  expect(wired).not.toContain('hl-inspector-inputs');
  expect(wired).toContain('hl-inspector-execution');
  expect(wired).toContain('aria-label="Parameters"');
  expect(render({})).not.toContain('hl-inspector-inputs');
  expect(render({value:spec})).toContain('设置内联值');
});
