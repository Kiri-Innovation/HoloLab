import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
let dir: string;
let video: Buffer;
let poster: Buffer;
test.beforeAll(() => {
  dir = mkdtempSync(join(tmpdir(), 'hololab-video-background-'));
  const path = join(dir, 'portrait.mp4');
  execFileSync('ffmpeg', ['-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=180x320:rate=15', '-t', '8', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', path]);
  video = readFileSync(path);
  execFileSync('ffmpeg', ['-v', 'error', '-i', path, '-frames:v', '1', join(dir, 'poster.png')]);
  poster = readFileSync(join(dir, 'poster.png'));
});
test.afterAll(() => rmSync(dir, { recursive: true, force: true }));
for (const theme of ['light', 'dark']) test(`video backgrounds ${theme}`, async ({ page }, info) => {
  await page.setViewportSize({ width: 720, height: 720 });
  await page.route('**/api/handles/video-background/summary', route => route.fulfill({ json: {
    storage: 'dir', fields: { entries: [{ name: 'portrait.mp4', is_dir: false, size_bytes: video.length }] },
  } }));
  await page.route('**/proxy/fixture/**', route => {
    if (route.request().url().includes('/_thumb/')) return route.fulfill({ contentType: 'image/png', body: poster });
    const range = route.request().headers()['range'];
    const start = Number(range?.match(/bytes=(\d+)-/)?.[1] ?? 0);
    return route.fulfill({ status: range ? 206 : 200, contentType: 'video/mp4',
      headers: { 'Accept-Ranges': 'bytes', ...(range ? { 'Content-Range': `bytes ${start}-${video.length - 1}/${video.length}` } : {}) },
      body: video.subarray(start) });
  });
  const url = `/e2e/fixtures/video-background.html?theme=${theme}`;
  await page.goto(url);
  const tile = page.locator('[data-hl-tile]');
  await tile.click();
  const shell = page.locator('[data-hl-video-array]');
  const overlay = page.locator('[data-hl-zoom-overlay]');
  const media = overlay.locator('video');
  await expect(page.locator('[data-hl-video-grid]')).toHaveCSS('visibility', 'hidden');
  await expect.poll(() => media.evaluate((v: HTMLVideoElement) => v.readyState)).toBeGreaterThanOrEqual(2);
  await page.locator('[data-hl-play]').click();
  await expect.poll(() => media.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeGreaterThan(.2);
  await page.locator('[data-hl-play]').click();
  const button = page.getByRole('button', { name: /^画面外背景/ });
  for (const mode of ['black', 'transparent', 'checkerboard']) {
    await expect(shell).toHaveAttribute('data-video-background', mode);
    await expect(media).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
    if (mode === 'black') await expect(overlay).toHaveCSS('background-color', 'rgb(0, 0, 0)');
    if (mode === 'transparent') {
      for (const element of [overlay, shell, page.locator('.hl-node-preview')])
        await expect(element).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
    }
    if (mode === 'checkerboard') await expect(overlay).toHaveCSS('background-image', /conic-gradient/);
    const controlBox = await page.locator('[data-hl-controls]').boundingBox();
    const buttonBox = await button.boundingBox();
    expect(buttonBox!.y + buttonBox!.height).toBeLessThan(controlBox!.y);
    await page.mouse.move(700, 700);
    await page.locator('.hololab-node').screenshot({ path: info.outputPath(`${theme}-${mode}.png`) });
    if (mode !== 'checkerboard') await button.click();
  }
  await page.locator('[data-hl-play]').click();
  await expect.poll(() => media.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeGreaterThan(.2);
  await button.focus();
  await page.keyboard.press('Space');
  await expect(shell).toHaveAttribute('data-video-background', 'black');
  await expect(page.locator('[data-hl-play]')).toHaveAttribute('data-hl-state', 'playing');
  await page.locator('[data-hl-play]').click();
  await page.locator('[data-hl-scrub]').fill('4');
  await expect.poll(() => media.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeCloseTo(4, 0);
  await button.click();
  await page.reload();
  await expect(shell).toHaveAttribute('data-video-background', 'transparent');
  await tile.click();
  await page.locator('[data-hl-zoom-back]').click();
  await expect(overlay).toHaveCount(0);
  await page.setViewportSize({ width: 360, height: 740 });
  await button.click();
  await expect(shell).toHaveAttribute('data-video-background', 'checkerboard');
});
