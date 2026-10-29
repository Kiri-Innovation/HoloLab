import { expect, test } from '@playwright/test';

for (const theme of ['light', 'dark']) test(`inline value toggle in ${theme}`, async ({ page }, info) => {
  await page.setViewportSize({ width:980, height:540 });
  await page.goto(`/e2e/fixtures/canvas-inline-values.html?theme=${theme}`);
  const collapsed = page.locator('.react-flow__node[data-id="collapsed"]');
  const expanded = page.locator('.react-flow__node[data-id="expanded"]');
  const collapsedToggle = collapsed.getByRole('button', { name:/amount：未连接，使用内联值/ });
  const expandedToggle = expanded.getByRole('button', { name:/amount：未连接，使用内联值/ });
  const collapsedWidth = await collapsed.evaluate(node => node.clientWidth);
  const expandedWidth = await expanded.evaluate(node => node.clientWidth);
  expect(collapsedWidth).toBe(220);
  expect(expandedWidth).toBe(collapsedWidth);
  await expect(collapsedToggle).toHaveAttribute('aria-expanded', 'false');
  await expect(expandedToggle).toHaveAttribute('aria-expanded', 'true');
  await expect(collapsed.locator('input[type="number"]')).toHaveCount(0);
  await expect(expanded.locator('input[type="number"]')).toHaveCount(1);
  await page.screenshot({ path:info.outputPath(`inline-values-${theme}.png`) });
  await collapsedToggle.click();
  await expect(collapsedToggle).toHaveAttribute('aria-expanded', 'true');
  await expect(collapsed.locator('input[type="number"]')).toHaveCount(1);
  await collapsedToggle.click();
  await expect(collapsedToggle).toHaveAttribute('aria-expanded', 'false');
});
