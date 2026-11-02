import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  // Dispatch tests isolate the iframe boundary; rendering is verified separately.
  await page.route('**/colmaputil/index.html?*', route => route.fulfill({contentType:'text/html',body:'<!doctype html><html></html>'}));
});

for (const suffix of ['', '&fallback=1', '&storage=dir&member=model.ftgs.ply']) {
  test(`FTGS spec/tag dispatch preserves the native filename ${suffix}`, async ({page}) => {
    await page.goto(`/e2e/fixtures/ftgsply-preview.html?viewer=ftgsply${suffix}`);
    const frame=page.getByTitle('FreeTimeGS PLY 4D viewer', {exact:true});
    await expect(frame).toBeVisible();
    const url=new URL((await frame.getAttribute('src'))!, 'http://test');
    expect(url.searchParams.get('embed')).toBe('ftgsply');
    expect(url.searchParams.get('src')).toBe('/sample/model.ftgs.ply' + (suffix.includes('storage=dir') ? '/model.ftgs.ply' : ''));
  });
}

test('static PLY retains its old entry even when the tag suggests FTGS', async ({page}) => {
  await page.goto('/e2e/fixtures/ftgsply-preview.html?viewer=ply-3dgs');
  const frame=page.getByTitle('3D Gaussian PLY', {exact:true});
  await expect(frame).toBeVisible();
  const params=new URL((await frame.getAttribute('src'))!, 'http://test').searchParams;
  expect(params.get('embed')).toBe('splatv');
  expect(params.get('format')).toBe('ply-3dgs');
});

test('explicit text override remains authoritative', async ({page}) => {
  await page.route('**/sample/model.ftgs.ply',route=>route.fulfill({body:'42',contentType:'text/plain'}));
  await page.goto('/e2e/fixtures/ftgsply-preview.html?viewer=text');
  await expect(page.locator('pre')).toContainText('42');
  await expect(page.locator('iframe')).toHaveCount(0);
});
