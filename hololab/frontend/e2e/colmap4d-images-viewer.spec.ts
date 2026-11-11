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
    const thumb = url.pathname.includes('/_thumb/');
    const name = decodeURIComponent(thumb ? url.pathname.split('/w/workflow/4d_images/')[1] : url.pathname.slice(root.length + 1)); requests.push((thumb ? 'thumb:' : '') + name);
    await route.fulfill(files[name] ? {body: files[name], contentType: name.endsWith('.png') ? 'image/png' : 'application/octet-stream'} : {status: 404});
  });
  await page.addInitScript(() => {
    (window as any).revoked = [];
    const revoke = URL.revokeObjectURL.bind(URL);
    URL.revokeObjectURL = url => { (window as any).revoked.push(url); revoke(url); };
  });
  // This HTTP origin also verifies that synthesis does not require WebCodecs.
  await page.goto('http://viewer.test/api/types/colmap4d-images/assets/viewer.html');
  await page.evaluate(url => window.postMessage({type: 'hololab-viewer-state', frames: [{proxy_url: url}]}, location.origin), root);
  return {requests, errors};
}
async function ready(page: Page) { await expect.poll(() => page.locator('video').evaluate(v => v.readyState)).toBeGreaterThanOrEqual(2); }
async function pixel(page: Page) {
  return page.locator('video').evaluate(video => {
    const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1;
    const ctx = canvas.getContext('2d')!; ctx.drawImage(video, 0, 0, 1, 1);
    return [...ctx.getImageData(0, 0, 1, 1).data];
  });
}

test('native playback controls replace the custom row; multi-camera switching remains', async ({page}) => {
  const {requests, errors} = await fixture(page, {
    'images/f000000__cam0.png': red, 'images/f000002__cam0.png': blue,
    'images/f000000__cam7.png': blue, 'images/f000002__cam7.png': red,
  });
  await ready(page);
  await expect(page.locator('#partitions')).toBeHidden();
  await expect(page.locator('#play, #seek, #fps, #loop, #position, footer')).toHaveCount(0);
  expect(await page.locator('video').evaluate(v => v.controls)).toBe(true);
  await expect(page.locator('#cameras')).toBeVisible();
  await expect.poll(async () => (await pixel(page))[0]).toBeGreaterThan(240);
  await page.locator('video').evaluate(v => { v.loop = false; return v.play(); });
  await expect.poll(() => page.locator('video').evaluate(v => v.ended)).toBe(true);
  await page.locator('video').evaluate(v => { v.currentTime = 1/30; });
  await expect.poll(async () => (await pixel(page))[2]).toBeGreaterThan(240);
  await page.locator('#camera').selectOption('cam7'); await ready(page);
  await expect.poll(async () => (await pixel(page))[2]).toBeGreaterThan(240);
  await page.locator('#camera').selectOption('cam0'); await ready(page);
  expect(await page.locator('video').getAttribute('data-cache-hit')).toBe('true');
  expect(requests.every(n => n.startsWith('thumb:'))).toBe(true);
  expect(await page.evaluate(() => (window as any).revoked.length)).toBeGreaterThan(0);
  expect(errors).toEqual([]);
});

test('canonical nested names retain time order, and a single wrapped shard has no pager', async ({page}) => {
  const meta = metadata([
    {id: 1, camera: 5, name: 'render/camera_5/later image.png', time: 1700000000000000002n},
    {id: 8, camera: 5, name: 'render/camera_5/earlier.png', time: 1700000000000000001n},
  ]);
  const prefix = 'p_0000000000000000/';
  const {errors} = await fixture(page, {
    [prefix + 'images.bin']: meta.images, [prefix + 'times.bin']: meta.times,
    [prefix + 'images/render/camera_5/later image.png']: blue,
    [prefix + 'images/render/camera_5/earlier.png']: red,
  });
  await ready(page); await expect(page.locator('#partitions')).toBeHidden();
  await expect(page.locator('#cameras')).toBeHidden();
  await expect.poll(async () => (await pixel(page))[0]).toBeGreaterThan(240);
  expect(await page.locator('video').evaluate(v => v.duration)).toBeCloseTo(2/30, 3);
  expect(errors).toEqual([]);
});

test('neighbours are warmed; paging reuses video blobs without downloading the selected frames again', async ({page}) => {
  const {requests, errors} = await fixture(page, {
    ...Object.fromEntries(Array.from({length: 12}, (_, i) => [`p_10/images/f${i}__cam0.png`, blue])),
    ...Object.fromEntries(Array.from({length: 12}, (_, i) => [`p_2/images/f${i}__cam0.png`, red])),
  });
  await ready(page); await expect(page.locator('nav')).toHaveCount(1);
  await expect(page.locator('#partition-label')).toHaveText('p_2  1 / 2');
  await expect(page.locator('#partitions')).toHaveAttribute('data-cached', '2');
  const count = requests.length;
  await page.locator('video').evaluate(v => v.play());
  await page.locator('#next').click(); await ready(page);
  await expect(page.locator('#partition-label')).toHaveText('p_10  2 / 2');
  await expect.poll(async () => (await pixel(page))[2]).toBeGreaterThan(240);
  await expect.poll(() => page.locator('video').evaluate(v => !v.paused)).toBe(true);
  expect(await page.locator('video').getAttribute('data-cache-hit')).toBe('true');
  expect(requests.length).toBe(count);
  await page.locator('#next').click(); await ready(page);
  await expect(page.locator('#partition-label')).toHaveText('p_2  1 / 2');
  await page.locator('#previous').click(); await ready(page);
  await expect(page.locator('#partition-label')).toHaveText('p_10  2 / 2');
  expect(requests.length).toBe(count); expect(errors).toEqual([]);
});

test('thumbnail unavailability falls back to originals and stale loading can be cancelled', async ({page}) => {
  await fixture(page, {'p_0/images/f0__cam0.png': red, 'p_1/images/f0__cam0.png': blue});
  await ready(page);
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/_thumb/**/p_1/images/*.png*', async route => {
    await gate; await route.fulfill({status: 501}).catch(() => {});
  });
  await page.locator('#next').click();
  await expect(page.locator('#status')).toBeVisible();
  await page.locator('#previous').click(); await ready(page);
  release();
  await expect.poll(async () => (await pixel(page))[0]).toBeGreaterThan(240);
  await page.locator('#next').click(); await ready(page);
  await expect.poll(async () => (await pixel(page))[2]).toBeGreaterThan(240);
});

test('LRU enforces both the byte limit and entry limit while retaining the active video', async ({page}) => {
  await fixture(page, {'images/f0__cam0.png': red}); await ready(page);
  const result = await page.evaluate(async () => {
    // @ts-ignore The type asset is served by the browser fixture.
    const {PreviewCache} = await import('/api/types/colmap4d-images/assets/preview-cache.js');
    const cache = new PreviewCache(3, 10);
    const add = (root: string, size: number, pin = '') => cache.put({root, camera: 'cam0', blob: new Blob(['x'.repeat(size)])}, pin);
    add('active', 4); const pin = cache.key('active', 'cam0');
    add('a', 3, pin); add('b', 3, pin); cache.get('a', 'cam0'); add('c', 3, pin);
    const byteEviction = !cache.get('b', 'cam0') && Boolean(cache.get('active', 'cam0')) && cache.bytes === 10;
    const oversized = !add('large', 11, pin);
    cache.clear(); for(let i=0;i<6;i++) add(String(i), 1);
    return {byteEviction, oversized, entries: cache.entries.size, bytes: cache.bytes};
  });
  expect(result).toEqual({byteEviction: true, oversized: true, entries: 3, bytes: 3});
});
