import {test, expect} from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

for (const version of ['0.4.8', '0.4.7']) test(`real scanned pack locations ${version}`, async ({page}) => {
  const rows = JSON.parse(fs.readFileSync(path.resolve('../../reports/multi-path-packs/api-evidence.json'), 'utf8'));
  const pack = rows.find((row: any) => row.version === version);
  await page.route('**/api/**', route => {
    const pathname = new URL(route.request().url()).pathname;
    return route.fulfill({json: pathname === '/api/pack-catalog' ? [pack] : pathname === '/api/nodes' ? [{node_id: 'n', node_name: 'A800 isolated evidence', packs: [pack], gpu: {count: 0}}] : []});
  });
  await page.goto(`/packs?view=local&section=category&pack=k4d%2Fstg-train%40${version}`);
  await expect(page.getByRole('button', {name: '复制路径', exact: true})).toHaveCount(1);
  const locations = page.getByTestId('pack-locations');
  if (version === '0.4.8') {
    await expect(locations).toContainText('当前生效 · 社区安装');
    await expect(locations).toContainText('未生效（被社区版覆盖） · 源码扫描');
    for (const location of pack.locations_by_node.n) await expect(locations).toContainText(location.pack_dir);
    await expect(locations).toContainText('清单相同不代表内容一致');
    await page.setViewportSize({width: 1440, height: 1200});
    await page.screenshot({path: '../../reports/multi-path-packs/page.png', fullPage: true});
  } else await expect(locations).toHaveCount(0);
});

for (const hashKind of ['manifest_hash', 'code_sha256']) test(`different ${hashKind} warning`, async ({page}) => {
  const rows = JSON.parse(fs.readFileSync(path.resolve('../../reports/multi-path-packs/api-evidence.json'), 'utf8'));
  const pack = rows.find((row: any) => row.version === '0.4.8');
  pack.locations_by_node.n[0][hashKind] = 'a'.repeat(64);
  pack.locations_by_node.n[1][hashKind] = 'b'.repeat(64);
  await page.route('**/api/**', route => {
    const pathname = new URL(route.request().url()).pathname;
    return route.fulfill({json: pathname === '/api/pack-catalog' ? [pack] : pathname === '/api/nodes' ? [{node_id: 'n', node_name: 'Test', packs: [pack], gpu: {count: 0}}] : []});
  });
  await page.goto('/packs?view=local&section=category&pack=k4d%2Fstg-train%400.4.8');
  await expect(page.getByTestId('pack-locations')).toContainText(hashKind === 'manifest_hash' ? '清单哈希不一致' : '内容哈希不一致');
});
