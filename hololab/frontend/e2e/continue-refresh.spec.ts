import { expect, test } from '@playwright/test';

test('Continue refreshes the same snapshot and analysis without websocket events', async ({ page }) => {
  const pack = { name: 'demo-echo', version: '0.1.0', manifest_hash: 'test', node_ids: ['compute'], inputs: {}, outputs: {}, params: {} };
  const graph = { nodes: ['A', 'B'].map((id, i) => ({ id, kind: 'algorithm', algorithm_name: pack.name, algorithm_version: pack.version, params: {}, assigned_node_id: 'compute', position: { x: 100 + i * 300, y: 100 } })), edges: [] };
  const job = (id: string) => ({ job_id: `job-${id}`, graph_node_id: id, workflow_id: 'continue', snapshot_id: 'same-run', node_id: 'compute', algorithm_name: pack.name, algorithm_version: pack.version, state: 'done', params: {}, input_handles: {}, output_handles: {}, created_ts: 1, updated_ts: 1 });
  let dispatched = false;
  let refreshed = 0;
  let analyzed = 0;
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.routeWebSocket('**', () => {});
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = [];
    if (path === '/api/pack-catalog') body = [pack];
    else if (path === '/api/nodes') body = [{ node_id: 'compute', node_name: 'Compute', packs: [pack], gpu: {}, connected_ts: 1 }];
    else if (path === '/api/nodes/metrics/history') body = { sample_interval_s: 3, nodes: {} };
    else if (path === '/api/workflows/continue') body = { workflow_id: 'continue', name: 'Continue', graph, created_ts: 1, updated_ts: 1 };
    else if (path === '/api/workflows' && route.request().method() === 'POST') body = { workflow_id: 'continue', updated_ts: 1 };
    else if (path === '/api/workflows/continue/runs') body = [{ snapshot_id: 'same-run', workflow_id: 'continue', created_ts: 1, node_count: 2, job_count: dispatched ? 2 : 1, state_counts: { done: dispatched ? 2 : 1 }, overall_state: 'done' }];
    else if (path === '/api/snapshots/same-run') {
      if (dispatched) refreshed++;
      body = { snapshot_id: 'same-run', workflow_id: 'continue', created_ts: 1, graph, jobs: dispatched ? [job('A'), job('B')] : [job('A')] };
    } else if (path === '/api/workflows/continue/prepare') body = { token: 'prepared' };
    else if (path === '/api/workflows/continue/dispatch/B') {
      dispatched = true;
      body = { snapshot_id: 'same-run', job_id: 'job-B', operation: 'continue' };
    } else if (path === '/api/workflows/continue/analyze') {
      if (dispatched) analyzed++;
      body = { graph, revision: 'same', staleness: {}, diff: [], environment_params: {}, canvas_jobs: dispatched ? [job('A'), job('B')] : [job('A')] };
    }
    await route.fulfill({ json: body });
  });
  await page.goto('/w/continue');
  await expect(page.getByTestId('rf__node-B')).toBeVisible();
  await expect(page.getByTestId('rf__node-A').locator('[data-state]')).toHaveAttribute('data-state', 'done');
  await page.evaluate(() => new Promise<void>((resolve, reject) => {
    window.dispatchEvent(new CustomEvent('hololab-run-node', { detail: { graph_node_id: 'B', resolve, reject } }));
  }));
  // Under the old [latestSnapshotId, reconnectTick] dependency this stays zero.
  await expect.poll(() => refreshed, { timeout: 3000 }).toBeGreaterThan(0);
  await expect.poll(() => analyzed, { timeout: 3000 }).toBeGreaterThan(0);
  await expect(page.getByTestId('rf__node-B').locator('[data-state]')).toHaveAttribute('data-state', 'done');
  expect(errors).toEqual([]);
});
