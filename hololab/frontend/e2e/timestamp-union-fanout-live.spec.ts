import { test, expect } from '@playwright/test';

// Exercise the deployed bundle and serialized catalog together. API routes for
// the scratch canvas are isolated so no user workflow or job is modified.
const liveURL = process.env.HOLOLAB_LIVE_URL;
test.skip(!liveURL, 'Set HOLOLAB_LIVE_URL to verify the deployed timestamp union contract');

test('rendered camera components connect to the timestamp union in fan-out', async ({ page, request }, info) => {
  const keys = ['vista4d/colmap4d-render-video@0.3.0', 'col4d/images-add-timestamp@0.2.0'];
  const catalog = (await Promise.all(keys.map(async key => {
    const response = await request.get(`${liveURL}/api/pack-catalog?key=${encodeURIComponent(key)}`);
    expect(response.ok()).toBe(true);
    return response.json();
  }))).flat();
  const attach = catalog.find(pack => pack.name === 'col4d/images-add-timestamp');
  expect(attach.inputs.timestamps.any_of.map((variant: { arrayed: boolean; dim_labels: string[] }) =>
    [variant.arrayed, variant.dim_labels])).toEqual([[false, []], [false, []]]);
  // A control port ensures we still reject genuinely different collection shapes.
  attach.inputs.mixed_timestamps = structuredClone(attach.inputs.timestamps);
  attach.inputs.mixed_timestamps.any_of[1].arrayed = true;
  attach.inputs.mixed_timestamps.any_of[1].dim_labels = ['frame'];
  const graph = { nodes: [
    { id: 'render', algorithm_name: keys[0].split('@')[0], algorithm_version: '0.3.0',
      arrayed_toggle: true, params: {}, position: { x: 0, y: 0 } },
    { id: 'attach', algorithm_name: attach.name, algorithm_version: attach.version,
      arrayed_toggle: true, params: {}, position: { x: 430, y: 0 } },
  ], edges: [] };
  const wid = 'timestamp-union-verification';
  await page.setViewportSize({ width: 1400, height: 900 });
  await page.routeWebSocket('**/*', () => {});
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = [];
    if (path === '/api/pack-catalog') body = catalog;
    if (path === `/api/workflows/${wid}`) body = {
      workflow_id: wid, name: 'Timestamp union fan-out verification', created_ts: 1, updated_ts: 1, graph,
    };
    if (path === '/api/nodes/metrics/history') body = { sample_interval_s: 5, nodes: {} };
    if (path === '/api/artifacts/summary') body = { total_bytes: 0, exclusive_bytes: 0, artifact_count: 0 };
    await route.fulfill({ json: body });
  });
  await page.goto(`${liveURL}/w/${wid}`);
  const source = page.locator('[data-id="render"] .source[data-handleid="4d_images"]');
  const target = (name: string) => page.locator(`[data-id="attach"] .target[data-handleid="${name}"]`);
  const feedback = page.locator('.hl-connection-tooltip');
  await expect(source).toBeVisible();
  await expect(target('timestamps')).toBeVisible();
  await source.hover(); await page.mouse.down(); await target('mixed_timestamps').hover();
  await expect(feedback).toHaveAttribute('data-valid', 'false');
  await expect(feedback).toContainText('不同数组形状的联合类型');
  await page.mouse.up();
  await expect(page.locator('.react-flow__edge')).toHaveCount(0);
  await source.hover(); await page.mouse.down(); await target('timestamps').hover();
  await expect(feedback).toHaveAttribute('data-valid', 'true');
  await expect(feedback).toContainText('可以连接');
  await page.screenshot({ path: info.outputPath('valid-drag.png') });
  await page.mouse.up();
  await expect(page.locator('.react-flow__edge')).toHaveCount(1);
  await page.screenshot({ path: info.outputPath('connected.png') });
});
