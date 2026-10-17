import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
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
  await page.setViewportSize({ width: 760, height: 520 });
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
  await expect.poll(() => media.evaluate((v: HTMLVideoElement) => v.readyState)).toBeGreaterThanOrEqual(2);
  await page.locator('[data-hl-play]').click();
  await expect.poll(() => media.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeGreaterThan(.2);
  await page.locator('[data-hl-play]').click();
  const button = page.getByRole('button', { name: /^画面外背景/ });
  const samplePixels = async () => {
    const v = (await media.boundingBox())!;
    const p = (await shell.boundingBox())!;
    // Region 1: shell padding; region 2: left pillarbox, away from controls.
    const points = [[p.x + 3, p.y + p.height / 2], [v.x + 12, v.y + v.height / 2],
      [v.x + 20, v.y + v.height / 2], [v.x + v.width / 2, v.y + v.height / 2]];
    return JSON.parse(execFileSync('python', ['-c',
      'from PIL import Image; import sys,io,json; im=Image.open(io.BytesIO(sys.stdin.buffer.read())); print(json.dumps([im.getpixel(tuple(p))[:3] for p in json.loads(sys.argv[1])]))',
      JSON.stringify(points.map(p => p.map(Math.round)))], { input: await page.screenshot() }).toString()) as number[][];
  };
  const geometry = await media.evaluate((v: HTMLVideoElement) => ({
    width: v.getBoundingClientRect().width, height: v.getBoundingClientRect().height,
    sourceWidth: v.videoWidth, sourceHeight: v.videoHeight, fit: getComputedStyle(v).objectFit,
  }));
  expect(geometry.fit).toBe('contain');
  expect(geometry.width / geometry.height).toBeGreaterThan(geometry.sourceWidth / geometry.sourceHeight);
  // A live DOM probe proves the side bars are CSS background, not encoded pixels.
  await media.evaluate(v => v.style.background = 'rgb(255, 0, 255)');
  expect((await samplePixels())[1]).toEqual([255, 0, 255]);
  await media.evaluate(v => v.style.background = 'var(--video-background)');
  const baseline = await samplePixels();
  const measurements = [];
  for (const mode of ['black', 'transparent', 'checkerboard']) {
    await expect(shell).toHaveAttribute('data-video-background', mode);
    const values = await media.evaluate(v => {
      const shell = v.closest('[data-hl-video-array]')!;
      const style = (e: Element) => ({ color: getComputedStyle(e).backgroundColor, image: getComputedStyle(e).backgroundImage });
      return { card: style(v.closest('.hololab-node')!), drawer: style(shell.parentElement!),
        region1: style(shell), parent: style(v.parentElement!), region2: style(v) };
    });
    const outerColor = theme === 'light' ? 'rgb(23, 32, 51)' : 'rgb(24, 24, 24)';
    expect(values.region1.color).toBe(outerColor);
    expect(values.drawer.color).toBe(outerColor);
    expect(values.parent.color).toBe('rgb(0, 0, 0)');
    expect(values.region2.color).toBe(mode === 'black' ? 'rgb(0, 0, 0)' : 'rgba(0, 0, 0, 0)');
    expect(values.region2.image.includes('conic-gradient')).toBe(mode === 'checkerboard');
    const pixels = await samplePixels();
    expect(pixels[0]).toEqual(baseline[0]);
    expect(pixels[3]).toEqual(baseline[3]); // Video content stays unchanged.
    if (mode === 'checkerboard') {
      expect([pixels[1], pixels[2]].sort()).toEqual((theme === 'light'
        ? [[216, 216, 216], [176, 176, 176]] : [[69, 69, 69], [48, 48, 48]]).sort());
    } else expect(pixels[1]).toEqual([0, 0, 0]);
    if (mode === 'transparent') {
      // Parent color must show through only the video letterbox.
      await overlay.evaluate(e => e.style.background = 'rgb(0, 128, 255)');
      expect((await samplePixels())[1]).toEqual([0, 128, 255]);
      expect((await samplePixels())[0]).toEqual(baseline[0]);
      await overlay.evaluate(e => e.style.background = '#000');
    }
    measurements.push({ mode, geometry, ...values, pixels: { region1: pixels[0], region2: pixels.slice(1, 3), content: pixels[3] } });
    const controlBox = await page.locator('[data-hl-controls]').boundingBox();
    const buttonBox = await button.boundingBox();
    expect(buttonBox!.y + buttonBox!.height).toBeLessThan(controlBox!.y);
    await page.mouse.move(740, 500);
    // DOM annotations are test-only and removed immediately after capture.
    await media.evaluate((v, { theme, mode, values }) => {
      const box = v.getBoundingClientRect();
      const shell = v.closest('[data-hl-video-array]')!.getBoundingClientRect();
      const notes = document.createElement('div'); notes.id = 'background-evidence';
      const label = (text: string, x: number, y: number) => {
        const el = document.createElement('div'); el.textContent = text;
        Object.assign(el.style, { position: 'fixed', left: `${x}px`, top: `${y}px`, zIndex: '9999',
          color: '#fff', background: '#163452', border: '1px solid #ffd54a', borderRadius: '3px',
          font: '12px/1.6 monospace', padding: '1px 5px', pointerEvents: 'none', whiteSpace: 'pre-wrap' });
        notes.appendChild(el);
      };
      label('1', shell.x - 7, box.y + 45);
      label('2', box.x + 24, box.y + box.height / 2 - 12);
      label('3', box.x + box.width / 2 - 8, box.y + box.height - 30);
      label(`${theme} / ${mode}\n\n1  Outside video: unchanged\n   ${values.region1.color}\n\n2  Inside video: CSS background\n   ${values.region2.color}\n   ${mode === 'checkerboard' ? '8px CSS checkerboard' : mode === 'transparent' ? 'Shows unchanged black parent' : 'Solid black'}\n\n3  Video pixels: unchanged`, 390, 165);
      document.body.appendChild(notes);
    }, { theme, mode, values });
    await page.screenshot({ path: info.outputPath(`${theme}-${mode}.png`) });
    await page.locator('#background-evidence').evaluate(e => e.remove());
    if (mode !== 'checkerboard') await button.click();
  }
  writeFileSync(info.outputPath(`${theme}-measurements.json`), JSON.stringify(measurements, null, 2));
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
