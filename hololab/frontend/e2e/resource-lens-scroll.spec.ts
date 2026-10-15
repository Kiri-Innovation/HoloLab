import { expect, test } from '@playwright/test';

test('bottom compute dock keeps the device visible while the lens scrolls', async ({ page }) => {
  page.on('pageerror', e => { throw e; });
  await page.routeWebSocket('**', () => {});
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = [];
    if (path === '/api/nodes') body = ['node-a', 'node-b'].map(node_id => ({ node_id, node_name: node_id === "node-a" ? "Studio GPU" : "Compute node with a deliberately long display name", packs: [], gpu: {count: 0}, connected_ts: 1, advertised_url: null, workspace_root: null, legacy_workspace_roots: [], flops_executor_id: null, packs_dir: null }));
    if (path === '/api/nodes/metrics/history') body = {sample_interval_s: 3, nodes: {'node-a': [{ts: 1, cpu_percent: 52.2, mem_used_gb: 20, mem_total_gb: 64, gpus: []}]}};
    if (path.endsWith('/resources/cpu')) body = {metric: 'cpu', ts: 1700000000, total_percent: 52.2, sample_window_s: .3, processes: Array.from({length: 32}, (_, i) => ({pid: 1000+i, name: `example-worker-${i+1}`, owner: 'external', usage_percent: 32-i, cmdline: `python worker.py --worker ${i+1}`, can_terminate: false})), gpus: [], error: null};
    await route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
  });
  await page.setViewportSize({width: 1440, height: 1000});
  await page.goto('/w/lens-scroll');
  const panel = page.locator('.hl-node-metrics');
  async function expectSettingsAtTopRight() {
    for (const card of await panel.locator('[data-pulse-node]').all()) {
      const gear = card.getByRole('button', {name: /^Node settings/});
      const bounds = (await card.boundingBox())!;
      const button = (await gear.boundingBox())!;
      expect(bounds.x + bounds.width - button.x - button.width).toBeCloseTo(13, 0);
      expect(button.y - bounds.y).toBeCloseTo(11, 0);
      const id = (await gear.locator('xpath=preceding-sibling::span[1]').boundingBox())!;
      expect(id.x + id.width).toBeLessThanOrEqual(button.x);
      const name = (await gear.locator("xpath=preceding-sibling::span[2]").boundingBox())!;
      expect(name.x + name.width).toBeLessThanOrEqual(id.x);
      expect(await card.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
    }
  }

  await expect(page.locator('.hl-compute-dock .hl-node-metrics')).toBeVisible();
  await expect(page.locator('.hl-inspector-sidebar .hl-node-metrics')).toHaveCount(0);
  const sidebar = await page.locator('.hl-inspector-sidebar').boundingBox();
  const dock = await page.locator('.hl-compute-dock').boundingBox();
  expect(sidebar!.y + sidebar!.height).toBe(1000);
  expect(dock!.x + dock!.width).toBeLessThanOrEqual(sidebar!.x);
  for (const theme of ['light', 'dark']) {
    await page.evaluate(t => document.documentElement.dataset.theme = t, theme);
    await expectSettingsAtTopRight();
    await page.screenshot({path: `test-results/pulse-closed-${theme}.png`});
  }
  await panel.getByRole('button', {name: 'Open CPU resource lens'}).first().click();
  const card = panel.locator('[data-pulse-node]');
  await expect(panel.locator('.hl-resource-process')).toHaveCount(32);
  await expectSettingsAtTopRight();
  const measurements: unknown[] = [];
  for (const theme of ['light', 'dark']) {
    await page.evaluate(t => document.documentElement.dataset.theme = t, theme);
    for (const [state, fraction] of [['top', 0], ['middle', .5], ['bottom', 1]] as const) {
      await panel.evaluate((el, f) => {el.scrollTop = (el.scrollHeight-el.clientHeight)*f;}, fraction);
      await page.waitForTimeout(100);
      const p = await panel.boundingBox(); const c = await card.boundingBox();
      // The bottom dock owns scrolling; the device stays pinned alongside the list.
      expect(await card.evaluate(el => getComputedStyle(el).position)).toBe('sticky');
      expect(c!.y).toBeGreaterThanOrEqual(p!.y);
      expect(c!.y).toBeLessThanOrEqual(p!.y + 40);
      expect(await panel.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
      if (fraction > 0) expect(await panel.evaluate(el => el.scrollTop)).toBeGreaterThan(0);
      measurements.push({theme, state, cardTop: c!.y, panelTop: p!.y, scrollTop: await panel.evaluate(el => el.scrollTop)});
      await page.screenshot({path: `test-results/lens-scroll-${theme}-${state}.png`});
    }
  }
  console.log(JSON.stringify(measurements));
  await page.keyboard.press('Escape');
  await expect(card).toHaveCount(2);
  await expect(panel.getByRole('button', {name: 'Open CPU resource lens'}).first()).toBeFocused();
  expect(await card.first().evaluate(el => getComputedStyle(el).position)).toBe('static');
  await panel.getByRole('button', {name: 'Open CPU resource lens'}).first().click();
  await page.setViewportSize({width: 1000, height: 1000});
  await expect(card).toHaveCount(1);
  expect(await card.evaluate(el => getComputedStyle(el).position)).toBe('static');
  expect(await panel.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
  await expectSettingsAtTopRight();
  await page.screenshot({path: 'test-results/lens-scroll-compact.png'});
  await page.setViewportSize({width: 390, height: 844});
  await page.getByRole('tab', {name: 'Nodes', exact: true}).click();
  await panel.getByRole('button', {name: 'Open CPU resource lens'}).first().click();
  await expect(card).toHaveCount(1);
  expect(await card.evaluate(el => getComputedStyle(el).position)).toBe('static');
  expect(await panel.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
  await expectSettingsAtTopRight();
  await page.screenshot({path: 'test-results/lens-scroll-narrow-top.png'});
  await panel.locator('.hl-resource-process').last().scrollIntoViewIfNeeded();
  await expect(panel.locator('.hl-resource-process').last()).toBeVisible();
  await page.screenshot({path: 'test-results/lens-scroll-narrow.png'});
});
