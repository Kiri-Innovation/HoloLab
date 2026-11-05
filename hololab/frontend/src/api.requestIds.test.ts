import { afterEach, expect, it, vi } from 'vitest';
import { dispatchNode, invalidateWorkflowPreparations, rerunFromNode, runWorkflow, warmNodePreparation } from './api';

afterEach(() => { vi.unstubAllGlobals(); });
it.each([false, true])('dispatch and rerun use valid request IDs (native randomUUID: %s)', async native => {
  let seed = 0;
  const randomUUID = vi.fn(() => '12345678-1234-4234-8234-123456789abc');
  const getRandomValues = vi.fn((bytes: Uint8Array) => { bytes.fill(++seed); return bytes; });
  vi.stubGlobal('crypto', native ? { randomUUID, getRandomValues } : { getRandomValues });
  const fetch = vi.fn<typeof globalThis.fetch>(async url => new Response(JSON.stringify(String(url).endsWith('/prepare') ? {token:'prepared'} : {}), {status:200}));
  vi.stubGlobal('fetch', fetch);
  await dispatchNode('workflow', 'node');
  await runWorkflow('workflow');
  await rerunFromNode('snapshot', 'node');
  const requests = fetch.mock.calls.filter(([url]) => !String(url).endsWith('/prepare'));
  expect(requests).toHaveLength(3);
  for (const [, options] of requests) {
    const headers = new Headers(options?.headers);
    expect(headers.get('X-Request-ID')).toMatch(/^[\da-f]{8}-[\da-f]{4}-4[\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/i);
  }
  expect(new Headers(requests[0][1]?.headers).get('X-Preparation-Token')).toBe('prepared');
  expect(new Headers(requests[1][1]?.headers).get('X-Preparation-Token')).toBe('prepared');
  // dispatch mints one additional ID for the unique Resource Timing URL;
  // it keeps simultaneous dispatches distinguishable in Chromium.
  expect(String(requests[0][0])).toContain('hl_timing=');
  if (native) expect(randomUUID).toHaveBeenCalledTimes(4);
  else expect(getRandomValues).toHaveBeenCalledTimes(4);
});

it('whole workflow reports every preparation blocker without submitting a run', async () => {
  const fetch = vi.fn(async () => new Response(JSON.stringify({token:'blocked',run_blockers:['a: missing alpha','b: missing beta']}), {status:200}));
  vi.stubGlobal('fetch', fetch);
  await expect(runWorkflow('workflow')).rejects.toThrow('a: missing alpha\nb: missing beta');
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(fetch.mock.calls[0]).toContain('/api/workflows/workflow/prepare');
});

it('binds a click preflight to the graph being saved and consumes that token', async () => {
  const graph = {nodes: [{id: 'node', kind: 'algorithm'}], edges: []} as any;
  const fetch = vi.fn<typeof globalThis.fetch>(async (url, options) => {
    void options;
    return new Response(
      JSON.stringify(String(url).endsWith('/prepare') ? {token: 'current-graph'} : {
        snapshot_id: 'snapshot', job_id: 'job', state: 'assigned',
        graph_node_id: 'node', algorithm_name: 'pack', algorithm_version: '1', operation: 'continue',
      }),
      {status: 200},
    );
  });
  vi.stubGlobal('fetch', fetch);

  await warmNodePreparation('workflow-graph', 'node', graph);
  await dispatchNode('workflow-graph', 'node', {graph});

  const prepareCalls = fetch.mock.calls.filter(([url]) => String(url).endsWith('/prepare'));
  expect(prepareCalls).toHaveLength(1);
  expect(JSON.parse(String(prepareCalls[0][1]?.body))).toEqual({node_id: 'node', graph});
  const dispatchCall = fetch.mock.calls.find(([url]) => String(url).includes('/dispatch/node'));
  expect(new Headers(dispatchCall?.[1]?.headers).get('X-Preparation-Token')).toBe('current-graph');
});

it('re-prepares the persisted draft once after a stale preparation token', async () => {
  const graph = {nodes: [{id: 'node', kind: 'algorithm'}], edges: []} as any;
  let dispatches = 0;
  const fetch = vi.fn<typeof globalThis.fetch>(async (url, options) => {
    const request = String(url);
    if (request.endsWith('/prepare')) {
      const body = JSON.parse(String(options?.body));
      return new Response(JSON.stringify({token: body.graph ? 'stale' : 'fresh'}), {status: 200});
    }
    dispatches += 1;
    if (dispatches === 1) {
      return new Response(JSON.stringify({detail: 'Preparation is stale; prepare the current configuration again'}), {status: 409});
    }
    return new Response(JSON.stringify({
      snapshot_id: 'snapshot', job_id: 'job', state: 'assigned',
      graph_node_id: 'node', algorithm_name: 'pack', algorithm_version: '1', operation: 'continue',
    }), {status: 200});
  });
  vi.stubGlobal('fetch', fetch);

  await warmNodePreparation('workflow-stale', 'node', graph);
  const result = await dispatchNode('workflow-stale', 'node', {graph});

  expect(result.client_timing.stale_retry).toBe(true);
  const prepareCalls = fetch.mock.calls.filter(([url]) => String(url).endsWith('/prepare'));
  expect(prepareCalls).toHaveLength(2);
  expect(JSON.parse(String(prepareCalls[0][1]?.body))).toEqual({node_id: 'node', graph});
  expect(JSON.parse(String(prepareCalls[1][1]?.body))).toEqual({node_id: 'node'});
});

it('does not let an old hover preparation survive a successful draft save', async () => {
  let resolveOld!: (value: {token: string}) => void;
  const old = new Promise<{token: string}>(resolve => { resolveOld = resolve; });
  let requestCount = 0;
  const fetch = vi.fn<typeof globalThis.fetch>(async () => {
    requestCount += 1;
    const value = requestCount === 1 ? await old : {token: 'fresh'};
    return new Response(JSON.stringify(value), {status: 200});
  });
  vi.stubGlobal('fetch', fetch);

  const hovered = warmNodePreparation('workflow-write', 'node');
  invalidateWorkflowPreparations('workflow-write');
  await warmNodePreparation('workflow-write', 'node');
  resolveOld({token: 'stale'});
  await hovered;
  await dispatchNode('workflow-write', 'node');

  const requestCalls = fetch.mock.calls;
  expect(requestCalls).toHaveLength(3);
  expect(new Headers(requestCalls[2][1]?.headers).get('X-Preparation-Token')).toBe('fresh');
});

it('classifies parameter preparation blockers without dispatching a job', async () => {
 const fetch=vi.fn(async()=>new Response(JSON.stringify({token:'blocked',run_blockers:['n (pack): 本地能力检查失败 prepare_params'],environment_checks:{n:{status:'unknown',issues:[{item:'prepare_params',required:{repo:null,optional:null},actual:'Parameter repo: enter a nonempty path string',remedy:'fill'}]}}}),{status:200}));
 vi.stubGlobal('fetch',fetch);
 await expect(runWorkflow('parameter-blocker')).rejects.toThrow('必填参数未填');
 expect(fetch).toHaveBeenCalledTimes(1);
});
