import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';

for (const theme of ['light', 'dark']) {
  test(`image alpha and opaque pixel regression ${theme}`, async ({ page }, info) => {
    await page.addInitScript(() => { delete (window as {IntersectionObserver?: unknown}).IntersectionObserver; });
    await page.goto('/e2e/fixtures/inspector-preview.html?mode=empty');
    const encoded = await page.evaluate(() => {
      const c = document.createElement('canvas'); c.width = 160; c.height = 90;
      const ctx = c.getContext('2d')!;
      ctx.fillStyle = '#888'; ctx.fillRect(40, 0, 120, 90);
      const alpha = c.toDataURL('image/png').split(',')[1];
      ctx.fillRect(0, 0, 160, 90);
      return {alpha, png:c.toDataURL('image/png').split(',')[1], jpeg:c.toDataURL('image/jpeg').split(',')[1]};
    });
    const alpha = process.env.HOLOLAB_ALPHA_IMAGE
      ? readFileSync(process.env.HOLOLAB_ALPHA_IMAGE) : Buffer.from(encoded.alpha, 'base64');
    let bytes = alpha;
    let mime = 'image/png';
    await page.route('**/fixture-image.svg', r => r.fulfill({body:bytes,contentType:mime}));
    const names = ['sample-a.png', 'sample-b.png', 'sample-c.png'];
    await page.route('**/api/handles/image/summary', r => r.fulfill({json:{fields:{entries:names.map(name => ({name,is_dir:false}))}}}));
    await page.route('**/proxy/compute/**', r => names.some(name => r.request().url().includes(name))
      ? r.fulfill({body:bytes,contentType:mime}) : r.fulfill({status:404}));
    for (const mode of ['default', 'sequence']) {
      bytes = alpha; mime = 'image/png';
      await page.goto(`/e2e/fixtures/inspector-preview.html?mode=${mode}&theme=${theme}`);
      const images = page.locator('img.hl-image-background');
      await expect.poll(() => images.count()).toBeGreaterThan(1);
      await expect.poll(() => images.evaluateAll(imgs => imgs.every(i => (i as HTMLImageElement).naturalWidth > 0))).toBe(true);
      expect(await images.first().evaluate(i => getComputedStyle(i).backgroundImage)).toContain('conic-gradient');
      await page.screenshot({path:info.outputPath(`rgba-${mode}-${theme}.png`)});
      if (mode === 'sequence') {
        await page.locator('[data-hololab-node="algorithm"] [data-hl-frame-card="0"]').click();
        await expect(page.locator('[data-hl-frame-zoom] img')).toBeVisible();
        await expect.poll(() => page.locator('[data-hl-frame-zoom] img').evaluate((i:HTMLImageElement) => i.naturalWidth)).toBeGreaterThan(0);
        await page.screenshot({path:info.outputPath(`rgba-zoom-${theme}.png`)});
      }
      for (const format of ['png', 'jpeg'] as const) {
        bytes = Buffer.from(encoded[format], 'base64'); mime = `image/${format}`;
        await page.reload();
        await page.mouse.move(800, 600);
        await page.addStyleTag({content:'*, *::before, *::after { animation: none !important; transition: none !important; }'});
        await expect.poll(() => images.evaluateAll(imgs => imgs.length > 1 && imgs.every(i => (i as HTMLImageElement).naturalWidth === 160))).toBe(true);
        // Compare complete preview regions, including shell and letterbox.
        for (const host of [page.locator('[data-hololab-node="algorithm"]'), page.locator('.hl-inspector-preview')]) {
          const root = mode === 'sequence' ? host.locator('[data-hl-frame-strip]')
            : host.locator('img.hl-image-background').locator('..');
          await expect(root.locator('img').first()).toBeVisible();
          const after = await root.screenshot({path:info.outputPath(`${mode}-${format}-backing.png`), animations:'disabled'});
          await root.locator('img.hl-image-background').evaluateAll(imgs => imgs.forEach(i => {
            const style = (i as HTMLElement).style;
            style.background = 'none';

          }));
          const before = await root.screenshot({path:info.outputPath(`${mode}-${format}-plain.png`), animations:'disabled'});
          // Only existing rounded-edge antialiasing may reveal the new backing.
          // All image content and surrounding UI must remain pixel-identical.
          const bounds = await root.locator('img').evaluateAll(imgs => imgs.map(i => {
            const a=i.getBoundingClientRect(), b=(i.closest('[data-hl-frame-strip]') || i.parentElement!).getBoundingClientRect();
            return [a.x-b.x,a.y-b.y,a.width,a.height,Math.max(parseFloat(getComputedStyle(i).borderRadius)||0,parseFloat(getComputedStyle(i.parentElement!).borderRadius)||0)];
          }));
          const diff = JSON.parse(execFileSync('python', ['-c',
            'import sys,json,base64,io; from PIL import Image,ImageChops; raw=json.load(sys.stdin); a,b=[Image.open(io.BytesIO(base64.b64decode(s))).convert("RGB") for s in raw[:2]]; d=ImageChops.difference(a,b); allowed=lambda x,y:any(r>0 and (x<l+r+1 or x>=l+w-r-1) and (y<t+r+1 or y>=t+h-r-1) and l-1<=x<l+w+1 and t-1<=y<t+h+1 for l,t,w,h,r in raw[2]); print(json.dumps({"outsideCorners":sum(d.getpixel((x,y))!=(0,0,0) and not allowed(x,y) for y in range(d.height) for x in range(d.width))}))',
          ], {input:JSON.stringify([before.toString('base64'),after.toString('base64'),bounds])}).toString());
          expect(diff.outsideCorners).toBe(0);
        }
      }
    }
  });
}

for (const theme of ['light', 'dark']) test(`nested and timeline alpha ${theme}`, async ({page}, info) => {
  await page.addInitScript(() => { delete (window as {IntersectionObserver?: unknown}).IntersectionObserver; });
  const body = process.env.HOLOLAB_ALPHA_IMAGE ? readFileSync(process.env.HOLOLAB_ALPHA_IMAGE)
    : Buffer.from('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7','base64');
  await page.route('**/proxy/alpha/**', r => r.fulfill({body,contentType:process.env.HOLOLAB_ALPHA_IMAGE ? 'image/png' : 'image/gif'}));
  await page.route('**/api/handles/alpha-nested/summary', r => r.fulfill({json:{fields:{entries:[{name:'camera',is_dir:true,entry_count:3,children:['a.png','b.png','c.png'].map(name => ({name,is_dir:false}))}]}}}));
  await page.goto(`/e2e/fixtures/image-alpha.html?theme=${theme}`);
  await expect(page.locator('img.hl-image-background')).toHaveCount(4);
  await expect.poll(() => page.locator('img').evaluateAll(imgs => imgs.every(i => i.naturalWidth > 0))).toBe(true);
  await page.screenshot({path:info.outputPath(`rgba-nested-timeline-${theme}.png`)});
  await page.locator('[data-hl-group-card]').click();
  await expect(page.locator('[data-hl-frame-card]')).toHaveCount(3);
  await expect.poll(() => page.locator('img').evaluateAll(imgs => imgs.every(i => i.naturalWidth > 0 && getComputedStyle(i).backgroundImage.includes('conic-gradient')))).toBe(true);
  await page.screenshot({path:info.outputPath(`rgba-nested-detail-${theme}.png`)});
  await page.locator('[data-hl-frame-card="0"]').click();
  await expect(page.locator('[data-hl-frame-zoom] img')).toHaveClass('hl-image-background');
});
