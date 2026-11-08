import {expect, test} from '@playwright/test';

test('text directory member still renders in canvas and inspector', async ({page}) => {
  await page.route('**/proxy/compute/result/output.text', r => r.fulfill({body:'Viewer regression text',contentType:'text/plain'}));
  await page.goto('/e2e/fixtures/inspector-preview.html?mode=text');
  await expect(page.locator('pre')).toHaveText(['Viewer regression text','Viewer regression text']);
});

test('binary content never renders as text', async ({page}) => {
  await page.route('**/proxy/compute/result/output.text', r => r.fulfill({
    body: Buffer.from([0x64, 0x5a, 0x00, 0x40, 0xff]), contentType: 'application/octet-stream',
  }));
  await page.goto('/e2e/fixtures/inspector-preview.html?mode=text');
  await expect(page.getByText('no preview available for binary content')).toHaveCount(2);
  await expect(page.locator('pre')).toHaveCount(0);
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

test('backend text decision is not overwritten by a splatv tag', async ({page}) => {
  const json = Buffer.from(JSON.stringify({width: 4,height: 4,cameras:[]}));
  const body = Buffer.alloc(8 + json.length);
  body.writeUInt16LE(0x674b); body.writeUInt32LE(json.length,4); json.copy(body,8);
  await page.route('**/proxy/compute/result/output.splatv', r => r.fulfill({body,contentType:'application/octet-stream'}));
  await page.route('**/colmaputil/index.html?*', r => r.fulfill({body:'<html><body>Splatv scene</body></html>',contentType:'text/html'}));
  await page.goto('/e2e/fixtures/inspector-preview.html?mode=splatv-text');
  await expect(page.locator('iframe')).toHaveCount(0);
  await expect(page.getByText('no preview available for binary content')).toHaveCount(2);
  await expect(page.locator('pre')).toHaveCount(0);
});
