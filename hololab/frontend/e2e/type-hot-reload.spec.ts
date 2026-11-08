import { expect, test, type WebSocketRoute } from '@playwright/test';

test('pack reload refreshes an open preview with the same pack hash and handle', async ({ page }) => {
  let generation = 'before';
  const sockets: WebSocketRoute[] = [];
  let catalogReads = 0;
  let handleReads = 0;
  const preview = () => ({ viewer: generation === 'before' ? 'text' : 'image', member: null });
  const pack = () => ({ name: 'type-test', version: '1.0.0', manifest_hash: 'unchanged', type_generation: generation,
    node_ids: ['compute'], inputs: {}, outputs: { result: { tags: ['custom'], storage: 'file', preview: preview() } }, params: {} });
  const graph = { nodes: [{ id: 'A', kind: 'algorithm', algorithm_name: 'type-test', algorithm_version: '1.0.0',
    params: {}, assigned_node_id: 'compute', position: { x: 100, y: 100 }, preview_open: 'result' }], edges: [] };
  const job = { job_id: 'job-A', graph_node_id: 'A', workflow_id: 'type-hot', snapshot_id: 'run', node_id: 'compute',
    algorithm_name: 'type-test', algorithm_version: '1.0.0', state: 'done', params: {}, input_handles: {}, output_handles: { result: 'h' }, created_ts: 1, updated_ts: 1 };
  await page.routeWebSocket('**', ws => { if (ws.url().endsWith('/ws/frontend')) sockets.push(ws); });
  await page.route('**/type-value.svg', route => route.fulfill({ contentType: 'image/svg+xml',
    body: '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><text>type-hot-marker</text></svg>' }));
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = [];
    if (path === '/api/pack-catalog') { catalogReads++; body = [pack()]; }
    else if (path === '/api/nodes') body = [{ node_id: 'compute', node_name: 'Compute', packs: [pack()], gpu: {}, connected_ts: 1 }];
    else if (path === '/api/nodes/metrics/history') body = { nodes: {} };
    else if (path === '/api/workflows/type-hot') body = { workflow_id: 'type-hot', name: 'Type hot reload', graph, created_ts: 1, updated_ts: 1 };
    else if (path === '/api/workflows' && route.request().method() === 'POST') body = { workflow_id: 'type-hot', updated_ts: 1 };
    else if (path === '/api/workflows/type-hot/runs') body = [{ snapshot_id: 'run', workflow_id: 'type-hot', created_ts: 1, node_count: 1, job_count: 1, state_counts: { done: 1 }, overall_state: 'done' }];
    else if (path === '/api/snapshots/run') body = { snapshot_id: 'run', workflow_id: 'type-hot', created_ts: 1, graph, jobs: [job] };
    else if (path.endsWith('/analyze')) body = { graph, revision: 'unchanged', staleness: {}, diff: [], environment_params: {}, canvas_jobs: [job] };
    else if (path === '/api/handles/facts') body = { facts: [{ handle_id: 'h', status: 'ready', facts: {} }] };
    else if (path === '/api/handles/h') {
      handleReads++;
      body = { handle_id: 'h', node_id: 'compute', storage: 'file', tags: ['custom'], proxy_url: '/type-value.svg', absolute_path: '/type-value.svg', deleted_ts: null, preview: preview(), dim_labels: null, dim_sizes: null };
    }
    await route.fulfill({ json: body });
  });
  await page.goto('/w/type-hot');
  const node = page.getByTestId('rf__node-A');
  await expect(node.locator('pre')).toContainText('type-hot-marker');
  const readsBefore = handleReads;
  // An update inside the catalog coalescing window must not be dropped.
  generation = 'after';
  expect(sockets.length).toBeGreaterThan(0);
  for (const socket of sockets) socket.send(JSON.stringify({ kind: 'node_online', payload: { node_id: 'compute', node_name: 'Compute', packs: [pack()] } }));
  await expect.poll(() => catalogReads).toBeGreaterThan(1);
  await expect(node.locator('img[src="/type-value.svg"]')).toBeVisible();
  await expect(node.locator('pre')).toHaveCount(0);
  expect(handleReads).toBeGreaterThan(readsBefore);
});
