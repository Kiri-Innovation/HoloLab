import {test, expect} from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const root = '/root/.hololab/node/packs/k4d/stg-train@0.4.10/content';
const dirs = [`${root}/packs/stg-train`, '/cloud/cloud-ssd1/Kiri4DGS/SpacetimeGaussians/packs/stg-train'];

for (const scenario of ['multiple', 'single', 'legacy', 'different-content'] as const) {
  test(`installation paths: ${scenario}`, async ({page}) => {
    // Reproduce the reported API shape without contacting any compute node.
    const locations = dirs.map((pack_dir, index) => ({pack_dir, manifest_path: `${pack_dir}/manifest.yaml`,
      root_abs: index === 0 ? root : '/cloud/cloud-ssd1/Kiri4DGS/SpacetimeGaussians',
      source_type: index === 0 ? 'community_install' : 'source_scan', effective: index === 0,
      manifest_hash: String(index).repeat(64), code_sha256: index === 0 ? 'a'.repeat(64) : scenario === 'different-content' ? 'b'.repeat(64) : null,
    }));
    const pack = {name: 'k4d/stg-train', version: '0.4.10', node_ids: ['n'], inputs: {}, outputs: {}, params: {},
      manifest_hash: '0'.repeat(64), source_dir: dirs[0], code_sha256: 'a'.repeat(64),
      content: {root_abs: root, files: 42, bytes: 2048},
      locations_by_node: scenario === 'legacy' ? undefined : {n: scenario === 'single' ? locations.slice(0, 1) : locations}};
    await page.addInitScript(() => {
      window.flops = {version: 1, showDocument: async () => ({success: true}), insertReference: async () => ({success: true})};
      Object.defineProperty(navigator, 'clipboard', {value: {writeText: async (value: string) => { (window as any).copied = value; }}});
    });
    await page.route('**/api/**', route => {
      const pathname = new URL(route.request().url()).pathname;
      return route.fulfill({json: pathname === '/api/pack-catalog' ? [pack] : pathname === '/api/nodes' ? [{node_id: 'n', node_name: 'Isolated fixture', packs: [pack], gpu: {count: 0}}] : []});
    });
    await page.route('**/ws**', route => route.abort());
    await page.goto('/packs?view=local&section=category&pack=k4d%2Fstg-train%400.4.10');
    const section = page.locator('details').filter({has: page.locator('summary', {hasText: /^安装与内容$/})});
    const multiple = scenario === 'multiple' || scenario === 'different-content';
    const expected = multiple ? dirs : [scenario === 'legacy' ? root : dirs[0]];
    await expect(section.locator('.hl-pack-command')).toHaveText(expected);
    await expect(section.getByRole('button', {name: '查看代码', exact: true})).toHaveCount(1);
    await section.getByRole('button', {name: '复制路径', exact: true}).click();
    expect(await page.evaluate(() => (window as any).copied)).toBe(expected[0]);
    await expect(section.getByRole('status').filter({hasText: '本地路径已复制。'})).toHaveCount(1);
    await expect(section.locator('dl')).toContainText('42 个文件 · 2.0 KB · sha256 aaaaaaaaaaaa…');
    if (multiple) {
      await expect(section.getByText('当前生效 · 社区安装', {exact: true})).toHaveCount(1);
      await expect(section.getByText('未生效（被社区版覆盖） · 源码扫描', {exact: true})).toBeVisible();
      await expect(section).toContainText('清单哈希不一致，两处清单不同。');
      await expect(section).toContainText(scenario === 'multiple' ? '未计算全部副本的内容哈希；清单相同不代表内容一致。' : '内容哈希不一致，两处内容不同。');
      await section.getByRole('button', {name: '复制副本路径', exact: true}).click();
      expect(await page.evaluate(() => (window as any).copied)).toBe(dirs[1]);
      await expect(section.getByRole('status').filter({hasText: '副本路径已复制。'})).toHaveCount(1);
    } else await expect(page.getByTestId('pack-locations')).toHaveCount(0);
    if (process.env.PACK_LOCATION_EVIDENCE) {
      const dest = path.resolve(process.env.PACK_LOCATION_EVIDENCE);
      fs.mkdirSync(dest, {recursive: true});
      fs.writeFileSync(path.join(dest, `${scenario}.json`), JSON.stringify({source: 'isolated API fixture reproducing reported 0.4.10 paths', paths: await section.locator('.hl-pack-command').allTextContents(), text: await section.innerText(), html: await section.evaluate(el => el.outerHTML)}, null, 2));
      await page.setViewportSize({width: 1440, height: 1000});
      await section.screenshot({path: path.join(dest, `${scenario}.png`)});
    }
  });
}
