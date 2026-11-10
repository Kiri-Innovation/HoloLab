import {test, expect, type Page} from '@playwright/test';
import {readFile} from 'node:fs/promises';

const assets = new URL('../../types/colmap4d-images/', import.meta.url);
const root = '/proxy/node/w/workflow/4d_images';
const red = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAIAAACQkWg2AAAAF0lEQVR4nGP8z0AaYCJR/aiGUQ1DSAMAQC4BH2bjRnMAAAAASUVORK5CYII=', 'base64');
const blue = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAIAAACQkWg2AAAAGUlEQVR4nGNkYPjPQApgIkn1qIZRDUNKAwA+MAEfWiW9ygAAAABJRU5ErkJggg==', 'base64');

function metadata(records: {id: number; camera: number; name: string; time: bigint}[]) {
  const count = Buffer.alloc(8); count.writeBigUInt64LE(BigInt(records.length));
  return {
    images: Buffer.concat([count, ...records.map(r => {
      const fixed = Buffer.alloc(64); fixed.writeUInt32LE(r.id); fixed.writeDoubleLE(1, 4); fixed.writeUInt32LE(r.camera, 60);
      return Buffer.concat([fixed, Buffer.from(r.name + '\0'), Buffer.alloc(8)]);
    })]),
    times: Buffer.concat([count, ...records.map(r => {
      const pair = Buffer.alloc(12); pair.writeUInt32LE(r.id); pair.writeBigInt64LE(r.time, 4); return pair;
    })]),
  };
}

async function fixture(page: Page, files: Record<string, Buffer>) {
  const requests: string[] = [], errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/api/types/colmap4d-images/assets/*', async route => {
    const name = new URL(route.request().url()).pathname.split('/').at(-1)!;
    await route.fulfill({body: await readFile(new URL(name, assets)), contentType: name.endsWith('.html') ? 'text/html' : 'text/javascript'});
  });
  await page.route('**/proxy/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/_glob')) {
      const base = url.searchParams.get('base')!.replace('w/workflow/4d_images', '').replace(/^\//, '');
      const pattern = url.searchParams.get('pattern')!;
      const regex = new RegExp('^' + pattern.replace(/[.+?^${}()|[\]\\]/g, '\\$&').replaceAll('*', '[^/]*') + '$');
      const names = Object.keys(files).filter(n => !base || n.startsWith(base + '/')).map(n => base ? n.slice(base.length + 1) : n).filter(n => regex.test(n));
      await route.fulfill({json: {files: names}}); return;
    }
    const name = decodeURIComponent(url.pathname.slice(root.length + 1)); requests.push(name);
    await route.fulfill(files[name] ? {body: files[name], contentType: name.endsWith('.png') ? 'image/png' : 'application/octet-stream'} : {status: 404});
  });
  await page.addInitScript(() => {
    (window as any).revoked = [];
    const revoke = URL.revokeObjectURL.bind(URL);
    URL.revokeObjectURL = url => { (window as any).revoked.push(url); revoke(url); };
  });
  // This HTTP origin also verifies that synthesis does not require WebCodecs.
  await page.goto('http://viewer.test/api/types/colmap4d-images/assets/viewer.html');
  await page.locator('#fps').fill('1');
  await page.evaluate(url => window.postMessage({type: 'hololab-viewer-state', frames: [{proxy_url: url}]}, location.origin), root);
  return {requests, errors};
}
async function ready(page: Page) { await expect(page.locator('#play')).toBeEnabled(); }
async function pixel(page: Page) {
  return page.locator('video').evaluate(video => {
    const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1;
    const ctx = canvas.getContext('2d')!; ctx.drawImage(video, 0, 0, 1, 1);
    return [...ctx.getImageData(0, 0, 1, 1).data];
  });
}

test('legacy multi-camera images become a seekable video with the existing controls', async ({page}) => {
  const {errors} = await fixture(page, {
    'images/f000000__cam0.png': red, 'images/f000002__cam0.png': blue,
    'images/f000000__cam7.png': blue, 'images/f000002__cam7.png': red,
  });
  await ready(page);
  await expect(page.locator('#partitions')).toBeHidden();
  await expect(page.locator('video')).toHaveCount(1); await expect(page.locator('img')).toHaveCount(0);
  expect(await page.locator('video').evaluate(v => v.duration)).toBeCloseTo(2, 3);
  await expect.poll(async () => (await pixel(page))[0]).toBeGreaterThan(240);
  await page.locator('#play').click();
  await expect.poll(() => page.locator('video').evaluate(v => v.currentTime)).toBeGreaterThan(0.1);
  await page.locator('#play').click();
  await page.locator('#seek').fill('1');
  await expect.poll(async () => (await pixel(page))[2]).toBeGreaterThan(240);
  await page.locator('#camera').selectOption('cam7'); await ready(page);
  await expect.poll(async () => (await pixel(page))[0]).toBeGreaterThan(240);
  await page.locator('#fps').fill('2'); await page.locator('#fps').dispatchEvent('change');
  expect(await page.locator('video').evaluate(v => v.playbackRate)).toBe(2);
  await page.locator('#loop').uncheck();
  await page.locator('#play').click();
  await expect.poll(() => page.locator('video').evaluate(v => v.ended)).toBe(true);
  await page.locator('#loop').check(); await page.locator('#play').click();
  await expect.poll(() => page.locator('video').evaluate(v => !v.paused && v.currentTime < 1.9)).toBe(true);
  expect(await page.evaluate(() => (window as any).revoked.length)).toBeGreaterThan(0);
  expect(errors).toEqual([]);
});

test('canonical nested names use camera IDs and nanosecond time order, including a single shard', async ({page}) => {
  const meta = metadata([
    {id: 1, camera: 5, name: 'render/camera_5/later image.png', time: 1700000000000000002n},
    {id: 8, camera: 5, name: 'render/camera_5/earlier.png', time: 1700000000000000001n},
    {id: 9, camera: 12, name: 'other/view.png', time: 1700000000000000001n},
  ]);
  const prefix = 'p_0000000000000000/';
  const {errors} = await fixture(page, {
    [prefix + 'images.bin']: meta.images, [prefix + 'times.bin']: meta.times,
    [prefix + 'images/render/camera_5/later image.png']: blue,
    [prefix + 'images/render/camera_5/earlier.png']: red, [prefix + 'images/other/view.png']: blue,
  });
  await ready(page); await expect(page.locator('#partitions')).toBeHidden();
  await expect(page.locator('#position')).toContainText('f8');
  await expect.poll(async () => (await pixel(page))[0]).toBeGreaterThan(240);
  await page.locator('#camera').selectOption('cam12'); await ready(page);
  await expect.poll(async () => (await pixel(page))[2]).toBeGreaterThan(240);
  expect(await page.locator('video').evaluate(v => v.duration)).toBeCloseTo(1, 3);
  expect(errors).toEqual([]);
});

test('fan-out has one wrapping pager and only encodes the selected shard', async ({page}) => {
  const {requests, errors} = await fixture(page, {
    'p_10/images/f0__cam0.png': blue, 'p_2/images/f0__cam0.png': red,
  });
  await ready(page); await expect(page.locator('nav')).toHaveCount(1);
  await expect(page.locator('#partition-label')).toHaveText('p_2  1 / 2');
  expect(requests.filter(n => n.endsWith('.png'))).toEqual(['p_2/images/f0__cam0.png']);
  await page.locator('#play').click();
  await page.locator('#next').click(); await ready(page);
  await expect(page.locator('#partition-label')).toHaveText('p_10  2 / 2');
  await expect.poll(async () => (await pixel(page))[2]).toBeGreaterThan(240);
  await expect.poll(() => page.locator('video').evaluate(v => !v.paused)).toBe(true);
  await page.locator('#next').click(); await ready(page);
  await expect(page.locator('#partition-label')).toHaveText('p_2  1 / 2');
  await page.locator('#previous').click(); await ready(page);
  await expect(page.locator('#partition-label')).toHaveText('p_10  2 / 2');
  await page.locator('video').hover(); await page.keyboard.press('ArrowRight'); await ready(page);
  await expect(page.locator('#partition-label')).toHaveText('p_2  1 / 2');
  expect(errors).toEqual([]);
});

test('switching during encoding cancels stale work and a bad shard can be left', async ({page}) => {
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  await fixture(page, {'p_0/images/f0__cam0.png': red, 'p_1/images/f0__cam0.png': blue});
  // Re-load after installing a delayed image route.
  await ready(page);
  await page.route('**/p_0/images/*.png', async route => {
    await gate; await route.fulfill({body: red, contentType: 'image/png'}).catch(() => {});
  });
  await page.locator('#next').click(); await ready(page);
  await page.locator('#previous').click();
  await expect(page.locator('#play')).toBeDisabled();
  await page.locator('#next').click(); await ready(page);
  release();
  await expect.poll(async () => (await pixel(page))[2]).toBeGreaterThan(240);
  await page.unroute('**/p_0/images/*.png');
  await page.route('**/p_0/images/*.png', route => route.fulfill({status: 404}));
  await page.locator('#previous').click(); await expect(page.locator('#status')).toContainText('HTTP 404');
  await page.locator('#next').click(); await ready(page);
  await expect.poll(async () => (await pixel(page))[2]).toBeGreaterThan(240);
});
