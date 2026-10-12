import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
const fixture = JSON.parse(readFileSync(new URL('./fixtures/connection-real-case.json', import.meta.url), 'utf8'));
const wid = '11111111-1111-1111-1111-111111111111';
for (const theme of ['light', 'dark']) {
  test(`drag validation anchored feedback ${theme}`, async ({ page }, info) => {
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.addInitScript(t => localStorage.setItem('hololab.theme', t), theme);
    await page.routeWebSocket('**/*', () => {});
    // Recorded real graph and manifest contracts; add a strict comparison port; the real STG input opts out.
    const catalog = structuredClone(fixture.catalog);
    const stg = catalog.find(p => p.name === 'stg-train')!;
    Object.assign(stg.inputs, { strict: { ...stg.inputs.colmap_frames, require_axis_label_match: true } });
    await page.route('**/api/**', async route => {
      const path = new URL(route.request().url()).pathname;
      let body: unknown = [];
      if (path === '/api/pack-catalog') body = catalog;
      if (path === `/api/workflows/${wid}`) body = { workflow_id: wid, name: 'Real merge_g0 → STG contract', created_ts: 1, updated_ts: 1, graph: fixture.graph };
      if (path === '/api/nodes/metrics/history') body = { sample_interval_s: 5, nodes: {} };
      if (path === '/api/artifacts/summary') body = { total_bytes: 0, exclusive_bytes: 0, artifact_count: 0 };
      await route.fulfill({ json: body });
    });
    await page.goto(`/w/${wid}`);
    const source = page.locator('[data-id="merge_g0"] .source[data-handleid="folder"]');
    const target = page.locator('[data-id="nmv4xtft71"] .target[data-handleid="strict"]');
    const good = page.locator('[data-id="nmv4xtft71"] .target[data-handleid="colmap_frames"]');
    await expect(source).toBeAttached();
    await page.waitForTimeout(300);
    const zoom = async (z: number) => page.locator('.react-flow__viewport').evaluate((el, z) => { (el as HTMLElement).style.transform = `translate(170px,150px) scale(${z})`; }, z);
    await zoom(1);
    const before = await source.boundingBox();
    const count = await page.locator('.react-flow__edge').count();
    await source.hover(); await page.mouse.down(); await target.hover();
    const feedback = page.locator('.hl-connection-tooltip');
    await expect(feedback).toHaveAttribute('data-valid', 'false');
    await expect(feedback).toContainText('数组轴名称不匹配。');
    await expect(feedback).toContainText('预期：arrayed[frame]<colmap-folder>');
    await expect(feedback).toContainText('实际：arrayed[group]<colmap-folder>');
    await page.screenshot({ path: info.outputPath(`${theme}-drag-invalid.png`) });
    await page.mouse.up();
    await expect(page.locator('.react-flow__edge')).toHaveCount(count);
    await expect(feedback).toBeVisible();
    await expect(feedback).toBeHidden({ timeout: 5500 });
    for (const z of [0.5, 1, 1.5]) {
      await zoom(z);
      await source.hover(); await page.mouse.down(); await good.hover();
      await expect(feedback).toHaveAttribute('data-valid', 'true');
      if (z === 1) await page.screenshot({ path: info.outputPath(`${theme}-drag-valid.png`) });
      await page.mouse.move(700, 150); await page.mouse.up();
      await expect(feedback).toBeHidden();
      await expect(page.locator('.react-flow__edge')).toHaveCount(count);
    }
    await zoom(1);
    expect(await source.boundingBox()).toEqual(before);
    await source.hover(); await page.mouse.down(); await good.hover(); await page.mouse.up();
    await expect(page.locator('.react-flow__edge')).toHaveCount(count + 1);
    await expect(feedback).toBeHidden();
    // Reverse dragging anchors at the hovered output, and Escape dismisses feedback.
    await target.hover(); await page.mouse.down(); await source.hover();
    await expect(feedback).toHaveAttribute('data-valid', 'false');
    const box = await page.getByRole('tooltip').boundingBox();
    const anchor = await source.boundingBox();
    expect(Math.abs((box!.x + box!.width / 2) - (anchor!.x + anchor!.width / 2))).toBeLessThan(2);
    await page.keyboard.press('Escape');
    await expect(feedback).toBeHidden();
    await page.mouse.move(700, 150); await page.mouse.up();
    await expect(page.locator('.react-flow__edge')).toHaveCount(count + 1);
    await zoom(1);
    await source.hover(); await expect(page.getByRole('tooltip')).toBeVisible();
    await expect(page.getByRole('tooltip')).toContainText('folder');
  });
}
