import { expect, test } from '@playwright/test';

for (const ext of ['png', 'jpg', 'jpeg', 'webp']) {
  test(`image sequence decodes ${ext} in canvas and inspector`, async ({ page }, info) => {
    await page.addInitScript(() => { delete (window as {IntersectionObserver?: unknown}).IntersectionObserver; });
    // Encode real image bytes using the browser, so decoding is tested too.
    await page.goto('/e2e/fixtures/inspector-preview.html?mode=empty');
    const mime = ext === 'jpg' || ext === 'jpeg' ? 'image/jpeg' : `image/${ext}`;
    const bytes = Buffer.from(await page.evaluate(mime => {
      const canvas = document.createElement('canvas');
      canvas.width = 64; canvas.height = 32;
      const ctx = canvas.getContext('2d')!;
      ctx.fillStyle = '#4b91dc'; ctx.fillRect(0, 0, 64, 32);
      return canvas.toDataURL(mime).split(',')[1];
    }, mime), 'base64');
    const names = Array.from({length: 50}, (_, i) => `p_${String(i).padStart(16, '0')}.${ext}`);
    await page.route('**/api/handles/image/summary', r => r.fulfill({json: {
      fields: {entries: [...names].reverse().map(name => ({name, is_dir: false})).concat([{name:'notes.txt',is_dir:false}])},
    }}));
    await page.route('**/proxy/compute/**', r => {
      const name = new URL(r.request().url()).pathname.split('/').pop()!;
      return names.includes(name)
        ? r.fulfill({contentType:mime, body:bytes})
        : r.fulfill({status:404});
    });
    await page.goto('/e2e/fixtures/inspector-preview.html?mode=sequence');
    for (const root of [page.locator('[data-hololab-node="algorithm"]'), page.locator('.hl-inspector-preview')]) {
      const strip = root.locator('[data-hl-frame-strip]');
      await expect(strip).toHaveAttribute('data-hl-frame-count', '50', {timeout:5000});
      await expect(strip.locator('img')).toHaveCount(6);
      await expect.poll(() => strip.locator('img').evaluateAll(imgs => imgs.every(img => (img as HTMLImageElement).naturalWidth === 64))).toBe(true);
      await expect(strip.locator('img').first()).toHaveAttribute('alt', names[0]);
      await expect(strip.locator('img').last()).toHaveAttribute('alt', names[49]);
    }
    await page.screenshot({path:info.outputPath(`${ext}-preview.png`)});
    await page.locator('[data-hololab-node="algorithm"] [data-hl-frame-card="49"]').click();
    const raw = page.locator(`img[src="/proxy/compute/overlay/${names[49]}"]`);
    await expect(raw).toBeVisible();
    await expect.poll(() => raw.evaluate((img: HTMLImageElement) => img.naturalWidth)).toBe(64);
  });
}
