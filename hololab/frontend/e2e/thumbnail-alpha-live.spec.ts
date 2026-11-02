import {expect, test} from '@playwright/test';

// Discovery metadata is a fixture; JPEGs and raw PNGs come from the running gateway.
// The gateway currently has intermittent concurrent Range-response failures.
const base = process.env.HOLOLAB_ALPHA_LIVE_BASE;
for (const theme of ['light','dark']) test(`live JPEG alpha tiles ${theme}`, async ({page}, info) => {
  test.skip(!base, 'Set HOLOLAB_ALPHA_LIVE_BASE to the temporary semantic/image.png directory proxy URL.');
  await page.setViewportSize({width:1000,height:520});
  await page.route('**/api/handles/alpha-live-fixture/summary', r => r.fulfill({json:{fields:{entries:[{name:'image.png',is_dir:false}]}}}));
  // These conventional filenames are absent in the semantic-name directory.
  await page.route('**/alpha-thumbnail-verification/semantic/**frame_000000.png', r => r.fulfill({status:404}));
  const jpegResponse = page.waitForResponse(r => r.url().includes('/_thumb/') && r.status()===200);
  await page.goto(`/e2e/fixtures/thumbnail-alpha-live.html?theme=${theme}&base=${encodeURIComponent(base!)}`);
  const response = await jpegResponse;
  expect(response.headers()['content-type']).toContain('image/jpeg');
  expect((await response.body()).subarray(0,2)).toEqual(Buffer.from([0xff,0xd8]));
  const tile = page.locator('[data-hl-frame-card="0"] img');
  await expect.poll(() => tile.evaluate((i:HTMLImageElement) => i.naturalWidth)).toBe(192);
  const samples = await tile.evaluate((i:HTMLImageElement) => {
    const c=document.createElement('canvas'); c.width=i.naturalWidth;c.height=i.naturalHeight;
    const ctx=c.getContext('2d')!;ctx.drawImage(i,0,0);
    return [4,12].map(x => [...ctx.getImageData(x,4,1,1).data].slice(0,3));
  });
  for (const [i,expected] of [216,176].entries())
    for(const channel of samples[i]) expect(Math.abs(channel-expected)).toBeLessThanOrEqual(5);
  await page.locator('main').screenshot({path:info.outputPath(`live-array-${theme}.png`)});
  await tile.click();
  await expect.poll(() => page.locator('[data-hl-frame-zoom] img').evaluate((i:HTMLImageElement)=>i.naturalWidth), {timeout:20_000}).toBe(3840);
  await page.locator('main').screenshot({path:info.outputPath(`live-zoom-${theme}.png`)});
});
