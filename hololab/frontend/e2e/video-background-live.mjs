// Real gateway/workflow + a separate fileserver running the modified code.
// No fixture media: requests are forwarded to that fileserver's actual endpoints.
// Start Vite at 15173 and the fileserver at 18829 before running this script.
// A second fileserver at 18830 serves landscape.mp4 and square.mp4 regression clips.
import { chromium } from 'playwright';
import { mkdirSync, writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import assert from 'node:assert/strict';
const output = process.env.EVIDENCE_DIR || '/tmp/hololab-unpadded';
mkdirSync(output, { recursive: true });
const browser = await chromium.launch();
try {
 const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
 await page.route('**/proxy/**', async route => {
   const url = new URL(route.request().url());
   const sub = url.pathname.replace(/^\/proxy\/[^/]+\//, '');
   const response = await route.fetch({ url: `http://127.0.0.1:18829/${sub}${url.search}` });
   await route.fulfill({ response });
 });
 await page.goto('http://127.0.0.1:15173/w/0eb1b412-e095-4f9d-b55a-5862cf9fc705');
 const card = page.locator('.hololab-node').first();
 const video = card.locator('[data-hl-tile] video').first();
 await video.waitFor();
 await page.locator('.react-flow__viewport').evaluate(e => e.style.transform = 'translate(40px,40px) scale(1.2294407894736843)');
 await page.locator('.react-flow__node-algorithm').evaluate(e => e.style.transform = 'translate(0px,0px)');
 await video.evaluate(async v => { await v.play(); v.pause(); await new Promise(resolve => { v.addEventListener('seeked', resolve, { once: true }); v.currentTime = 6; }); });
 const geometry = await video.evaluate(v => ({ width: v.videoWidth, height: v.videoHeight, rect: v.getBoundingClientRect().toJSON(), fit: getComputedStyle(v).objectFit, source: v.currentSrc, poster: v.poster }));
 assert.equal(geometry.width, 154); assert.equal(geometry.height, 180); assert.equal(geometry.fit, 'contain');
 const evidence = { geometry, modes: [] };
 const button = card.getByRole('button', { name: /^画面外背景/ });
 for (const theme of ['light', 'dark']) {
  await page.evaluate(theme => document.documentElement.dataset.theme = theme, theme);
  for (const mode of ['black', 'transparent', 'checkerboard']) {
   for (let i = 0; i < 3 && await card.locator('[data-hl-video-array]').getAttribute('data-video-background') !== mode; i++) await button.click();
   await page.mouse.move(1400, 900);
   const styles = await video.evaluate(v => ({ color: getComputedStyle(v).backgroundColor, image: getComputedStyle(v).backgroundImage, outer: getComputedStyle(v.closest('[data-hl-video-array]')).backgroundColor }));
   const box = await video.boundingBox();
   const shell = await card.locator('[data-hl-video-array]').boundingBox();
   const points = [[box.x + 12, box.y + box.height / 2], [box.x + 22, box.y + box.height / 2], [shell.x + 3, box.y + box.height / 2]];
   const pixels = JSON.parse(execFileSync('python', ['-c', 'from PIL import Image; import sys,io,json; im=Image.open(io.BytesIO(sys.stdin.buffer.read())); print(json.dumps([im.getpixel(tuple(p))[:3] for p in json.loads(sys.argv[1])]))', JSON.stringify(points.map(p => p.map(Math.round)))], { input: await page.screenshot() }).toString());
   const outer = theme === 'light' ? [23, 32, 51] : [24, 24, 24];
   assert.deepEqual(pixels[2], outer);
   if (mode === 'black') assert.deepEqual(pixels[0], [0, 0, 0]);
   if (mode === 'transparent') assert.deepEqual(pixels[0], outer);
   if (mode === 'checkerboard') {
    const palette = theme === 'light' ? [[216,216,216],[176,176,176]] : [[69,69,69],[48,48,48]];
    assert.deepEqual(pixels.slice(0, 2).sort(), palette.sort());
   }
   evidence.modes.push({ theme, mode, styles, points, pixels });
   await page.locator('.hololab-node').screenshot({ path: `${output}/${theme}-${mode}.png` });
  }
 }
 // Exercise existing controls on the real grid and original-video zoom.
 await card.locator('[data-hl-play]').click();
 await page.waitForFunction(() => document.querySelector('[data-hl-tile] video').currentTime > 6.2);
 await card.locator('[data-hl-play]').click();
 await card.locator('[data-hl-scrub]').fill('12');
 await page.waitForFunction(() => Math.abs(document.querySelector('[data-hl-tile] video').currentTime - 12) < .2);
 await card.locator('[data-hl-tile]').click();
 await card.locator('[data-hl-zoom-overlay] video').waitFor();
 await card.locator('[data-hl-zoom-back]').click();
 assert.equal(await card.locator('[data-hl-zoom-overlay]').count(), 0);
 evidence.controls = 'play / pause / seek to 12s / zoom / back passed; grid has no fullscreen button';
 evidence.regression = [];
 for (const [name, expectedWidth, expectedHeight] of [['landscape', 320, 180], ['square', 180, 180]]) {
  await video.evaluate(async (v, name) => {
   v.poster = `http://127.0.0.1:18830/_thumb/320x180/${name}.mp4?at=0&unpadded=true`;
   v.src = `http://127.0.0.1:18830/_preview/320x180/${name}.mp4?fps=15&v=2-contain`;
   await v.play();
  }, name);
  await page.waitForFunction(() => document.querySelector('[data-hl-tile] video').currentTime > .1);
  await video.evaluate(v => v.pause());
  const result = await video.evaluate(v => ({width: v.videoWidth, height: v.videoHeight, fit: getComputedStyle(v).objectFit, box: v.getBoundingClientRect().toJSON()}));
  assert.equal(result.width, expectedWidth); assert.equal(result.height, expectedHeight);
  assert.equal(result.fit, 'contain'); assert.equal(result.box.width, geometry.rect.width);
  evidence.regression.push({name, ...result});
  await card.screenshot({path: `${output}/${name}.png`});
 }
 writeFileSync(`${output}/browser.json`, JSON.stringify(evidence, null, 2));
 await page.unrouteAll({ behavior: "ignoreErrors" });
 console.log("Real-video backgrounds and playback checks passed.");
} finally { await browser.close(); }
