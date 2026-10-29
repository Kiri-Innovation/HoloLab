import { expect, test } from '@playwright/test';

for (const theme of ['light', 'dark']) test(`inline input editor toggle in ${theme}`, async ({ page }, info) => {
  await page.setViewportSize({ width: 480, height: 360 });
  await page.goto(`/e2e/fixtures/inline-input-editor.html?theme=${theme}`);
  const explicit = page.getByRole('button', { name: /explicit：内联值/ });
  const fallback = page.getByRole('button', { name: /fallback：端口默认值/ });
  const format = page.getByRole('button', { name: /format：类型默认值/ });
  const upstream = page.getByRole('button', { name: /upstream：未设置/ });
  await expect(explicit).toHaveAttribute('aria-expanded', 'false');
  await expect(fallback).toHaveAttribute('aria-expanded', 'false');
  await expect(format).toHaveAttribute('aria-expanded', 'false');
  await expect(upstream).toBeDisabled();
  await expect(page.locator('input[type="number"]')).toHaveCount(0);
  await explicit.click();
  await expect(explicit).toHaveAttribute('aria-expanded', 'true');
  await expect(page.locator('input[type="number"]')).toHaveCount(1);
  await page.screenshot({ path: info.outputPath(`inline-input-editor-${theme}.png`) });
  await explicit.click();
  await expect(explicit).toHaveAttribute('aria-expanded', 'false');
  await expect(page.locator('input[type="number"]')).toHaveCount(0);
});
