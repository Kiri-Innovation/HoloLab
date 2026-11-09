import { test, expect } from '@playwright/test';

for (const legacy of [false, true]) {
  for (const name of ['community/video-trim', 'builtin/slice']) {
    test(`${name} prepares independently of missing packs (${legacy ? 'legacy assignment' : 'logical device'})`, async ({ page }) => {
      const packs = [
        { name: 'community/video-trim', version: '0.1.0', manifest_hash: 'video-present', node_ids: ['worker'],
          description: 'Trim a video', inputs: {}, outputs: { video: { tags: ['video'], storage: 'file', preview: null } },
          params: { start_seconds: { type: 'float', default: 43 }, duration_seconds: { type: 'float', default: 5 } } },
        { name: 'builtin/slice', version: '0.1.0', manifest_hash: 'slice-present', node_ids: ['worker'],
          description: 'Slice an array', inputs: {}, outputs: { out: { tags: ['any'], storage: 'dir', arrayed: true, preview: null } },
          params: { start: { type: 'int', default: 0 }, end: { type: 'int', default: 10 }, step: { type: 'int', default: 1 } } },
      ];
      const nodes = [
        ...packs.map((pack, index) => ({ id: pack.name, algorithm_name: pack.name, algorithm_version: pack.version,
          position: { x: index * 400, y: 0 }, params: {},
          ...(legacy ? { assigned_node_id: 'worker' } : { assigned_node_id: null, device: 'default' }) })),
        ...Array.from({ length: 12 }, (_, index) => ({ id: `missing-${index}`, algorithm_name: `remote/missing-${index}`,
          algorithm_version: '0.1.0', position: { x: index * 300, y: 450 }, params: {}, assigned_node_id: null })),
      ];
      const graph = { nodes, edges: [] };
      const issues = nodes.slice(2).map(node => ({ node_id: node.id, message: 'Algorithm declaration unavailable' }));
      const declarations: string[] = [];
      const dispatches: string[] = [];
      const errors: string[] = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.routeWebSocket('**', () => {});
      await page.route('**/api/**', async route => {
        const pathname = new URL(route.request().url()).pathname;
        let body: unknown = [];
        if (pathname === '/api/pack-catalog') body = packs;
        else if (pathname === '/api/nodes') body = [{ node_id: 'worker', node_name: 'Worker', gpu: { count: 0 }, packs }];
        else if (pathname === '/api/nodes/metrics/history') body = { nodes: {} };
        else if (pathname === '/api/workflows/scoped') body = { workflow_id: 'scoped', name: 'Cross-machine graph', graph, rev: 0, missing: false, updated_ts: 1 };
        else if (pathname.endsWith('/device-bindings')) body = { workflow_id: 'scoped', revision: 0, bindings: {} };
        else if (pathname.endsWith('/canvas-state') || pathname.endsWith('/analyze')) body = { graph, revision: 'current', canvas_jobs: [], issues, staleness: {}, diff: [], environment_params: {} };
        else if (pathname.endsWith('/prepare')) {
          const request = route.request().postDataJSON();
          declarations.push(request.node_id);
          // The running old gateway returns all twelve issues even though
          // the requested package has complete ports/parameters and a token.
          body = { token: 'prepared-target', graph, issues, run_blockers: issues.map(issue => issue.message), environment_checks: {} };
        } else if (pathname.includes('/dispatch/')) {
          dispatches.push(decodeURIComponent(pathname.split('/dispatch/')[1]));
          body = { snapshot_id: 'snapshot', job_id: 'job', state: 'assigned', operation: 'continue', graph_node_id: name };
        } else if (pathname === '/api/snapshots/snapshot') body = { snapshot_id: 'snapshot', workflow_id: 'scoped', graph, jobs: [], created_ts: 1 };
        await route.fulfill({ json: body });
      });
      await page.goto('/w/scoped');
      const card = page.locator('.react-flow__node').filter({ has: page.locator(`[data-hololab-node="algorithm"]`) })
        .filter({ hasText: name.split('/')[1] }).first();
      await expect(card).toBeVisible();
      await card.evaluate(element => (element as HTMLElement).click());
      const inspector = page.locator('.hl-node-inspector');
      await expect(inspector).toContainText(name);
      for (const parameter of name === 'community/video-trim' ? ['start_seconds', 'duration_seconds'] : ['start', 'end', 'step']) {
        await expect(inspector.getByRole('spinbutton', { name: parameter, exact: true })).toBeVisible();
      }
      const run = card.locator('.hl-node-run-control');
      await expect(run).toBeEnabled();
      await run.evaluate(element => (element as HTMLElement).click());
      await expect.poll(() => dispatches).toEqual([name]);
      expect(declarations.every(nodeId => nodeId === name)).toBe(true);
      await expect(card).not.toContainText('Algorithm declaration unavailable');
      expect(errors).toEqual([]);
    });
  }
}
