import { test, expect } from '@playwright/test';

for (const theme of ['light', 'dark']) test(`environment readiness in ${theme}`, async ({ page }, info) => {
  await page.setViewportSize({width: 1100, height: 760});
  await page.goto(`/e2e/fixtures/environment-readiness.html?theme=${theme}`);
  const card = (id: string) => page.locator(`.react-flow__node[data-id="${id}"]`);
  const status = (id: string) => card(id).locator('[data-hl-node-status]');

  const missing = card('missing');
  await expect(status('missing')).toHaveAttribute('data-status-tone', 'neutral');
  await expect(status('missing').locator('.hl-node-status-environment-missing')).toHaveText('!');
  const run = missing.getByRole('button', {name: '运行', exact: true});
  await expect(run).toBeDisabled();
  await run.hover({force: true});
  await expect(page.getByRole('tooltip')).toContainText('本地能力检查失败');
  await expect(page.getByRole('tooltip')).toContainText('当前配置不能启动新任务');
  await status('missing').hover();
  await expect(page.getByRole('tooltip')).toContainText('check-pack@1.0.0');
  await expect(page.getByRole('tooltip')).toContainText('package:demo: 要求=demo>=2; 当前=未安装');
  await page.screenshot({path: info.outputPath(`environment-missing-${theme}.png`)});

  await expect(status('normal').locator('.hl-node-status-environment-missing')).toHaveCount(0);
  await expect(card('normal').getByRole('button', {name: '运行', exact: true})).toBeEnabled();
  await expect(status('running')).toHaveAttribute('data-status-tone', 'info');
  await expect(status('running').locator('.hl-node-status-environment-missing')).toHaveCount(0);
  await expect(status('running').locator('.hl-node-status-dot')).toHaveCSS('background-color', /rgb/);
  for (const id of ['unknown', 'undeclared']) {
    await expect(status(id).locator('.hl-node-status-environment-missing')).toHaveCount(0);
    await expect(card(id).getByRole('button', {name: '运行', exact: true})).toBeEnabled();
  }
  await expect(status('upstream').locator('.hl-node-status-environment-missing')).toHaveCount(0);
  await expect(card('upstream').getByRole('button', {name: '运行', exact: true})).toBeDisabled();
});
