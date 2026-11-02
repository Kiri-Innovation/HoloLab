import {expect, test} from '@playwright/test';

test('text directory member still renders in canvas and inspector', async ({page}) => {
  await page.route('**/proxy/compute/result/output.text', r => r.fulfill({body:'Viewer regression text',contentType:'text/plain'}));
  await page.goto('/e2e/fixtures/inspector-preview.html?mode=text');
  await expect(page.locator('pre')).toHaveText(['Viewer regression text','Viewer regression text']);
});

test('splatv header still loads and dispatches the scene iframe', async ({page}) => {
  const json = Buffer.from(JSON.stringify({width: 4,height: 4,cameras:[]}));
  const body = Buffer.alloc(8 + json.length);
  body.writeUInt16LE(0x674b); body.writeUInt32LE(json.length,4); json.copy(body,8);
  await page.route('**/proxy/compute/result/output.splatv', r => r.fulfill({body,contentType:'application/octet-stream'}));
  // Isolate dispatch/header decoding from the unchanged WebGL renderer.
  await page.route('**/colmaputil/index.html?*', r => r.fulfill({body:'<html><body>Splatv scene</body></html>',contentType:'text/html'}));
  await page.goto('/e2e/fixtures/inspector-preview.html?mode=splatv');
  await expect(page.locator('iframe')).toHaveCount(2);
  for (const iframe of await page.locator('iframe').all()) {
    await expect(iframe).toHaveAttribute('src', /embed=splatv.*src=%2Fproxy%2Fcompute%2Fresult%2Foutput.splatv/);
    await expect(iframe.contentFrame().locator('body')).toHaveText('Splatv scene');
  }
});
