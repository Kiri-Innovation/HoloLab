import { test, expect } from '@playwright/test';

for (const theme of ['light', 'dark']) {
  test(`snapshot remains readable after pack retirement ${theme}`, async ({ page }, info) => {
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.setViewportSize({ width: 1200, height: 800 });
    await page.route('**/api/**', route => route.fulfill({ json: {} }));
    await page.goto(`/e2e/fixtures/type-ir-snapshot.html?theme=${theme}&missingPack&legacy`);
    const nodes = page.locator('.react-flow__node');
    await expect(nodes).toHaveCount(2);
    await expect(nodes.first()).toContainText('merge-colmap');
    // The historical identity survives; absent manifests cannot reconstruct ports.
    await expect(nodes.first()).toContainText('0.4.0');
    await expect(nodes.locator('.react-flow__handle')).toHaveCount(0);
    await nodes.first().click();
    await expect(nodes.first()).toHaveClass(/selected/);
    expect(errors).toEqual([]);
    await page.screenshot({ path: info.outputPath(`${theme}-retired-pack-snapshot.png`) });
  });
}
