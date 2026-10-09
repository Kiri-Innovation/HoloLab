// Serve a completed frontend build on an independent port before running this.
import { chromium } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const origin = new URL(process.argv[2] || 'http://127.0.0.1:15173').origin;
const evidence = path.resolve(process.argv[3] || 'test-results/colmap-vendor');
fs.mkdirSync(evidence, { recursive: true });
const browser = await chromium.launch({
  headless: true,
  args: ['--no-sandbox', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
});

try {
  const page = await browser.newPage({ viewport: { width: 1100, height: 800 } });
  const failures = [];
  const errors = [];
  const responses = [];
  page.on('response', response => {
    responses.push({ status: response.status(), url: response.url() });
    if (response.status() >= 400) failures.push(`${response.status()} ${response.url()}`);
  });
  page.on('requestfailed', request => failures.push(`${request.url()}: ${request.failure()?.errorText}`));
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    window.__draws = [];
    for (const klass of [window.WebGLRenderingContext, window.WebGL2RenderingContext]) {
      if (!klass) continue;
      for (const name of ['drawArrays', 'drawElements', 'drawArraysInstanced', 'drawElementsInstanced']) {
        const original = klass.prototype[name];
        if (!original) continue;
        klass.prototype[name] = function (...args) {
          if (window.__draws.length < 10000) {
            window.__draws.push({
              method: name,
              mode: args[0],
              count: args[name.startsWith('drawArrays') ? 2 : 1],
              instances: name.endsWith('Instanced') ? args[name.startsWith('drawArrays') ? 3 : 4] : null,
            });
          }
          return original.apply(this, args);
        };
      }
    }
  });
  // Only the host fixture is intercepted. The iframe and all assets are real
  // production files served by Vite preview, with no gateway or API mocks.
  await page.route('**/__vendor_smoke', route => route.fulfill({
    contentType: 'text/html',
    body: `<!doctype html><html><body style="margin:0">
      <script>
        window.ready = false;
        addEventListener('message', event => {
          if (event.data?.type === 'colmap-ready') window.ready = true;
        });
      </script>
      <iframe style="width:1090px;height:780px;border:0"
        src="/colmaputil/index.html?embed=1"></iframe>
      </body></html>`,
  }));
  await page.goto(origin + '/__vendor_smoke');
  await page.waitForFunction(() => window.ready);
  const points = Array.from({ length: 125 }, (_, i) =>
    `${i + 1} ${(i % 5 - 2) * .3} ${(Math.floor(i / 5) % 5 - 2) * .3} ${(Math.floor(i / 25) - 2) * .3} ${i % 2 ? 255 : 50} ${i % 2 ? 70 : 240} 80 0.1 1 0`
  ).join('\n');
  await page.evaluate(({ points }) => {
    const data = {
      'cameras.txt': '1 PINHOLE 640 480 500 500 320 240\n',
      'images.txt': '1 1 0 0 0 0 0 3 1 one.jpg\n320 240 1\n2 1 0 0 0 1 0 3 1 two.jpg\n320 240 2\n3 1 0 0 0 -1 0 3 1 three.jpg\n320 240 3\n',
      'points3D.txt': points + '\n',
    };
    document.querySelector('iframe').contentWindow.postMessage({
      type: 'colmap-load-files',
      name: 'build-smoke',
      files: Object.entries(data).map(([name, text]) => ({ name, blob: new Blob([text]) })),
    }, location.origin);
  }, { points });
  const frame = page.frames().find(candidate => candidate.url().includes('/colmaputil/'));
  await frame.waitForSelector('canvas');
  // The renderer uses instanced quads for points, and 16 line vertices per pose.
  await frame.waitForFunction(() => window.__draws.some(draw => draw.instances === 125));
  await frame.waitForFunction(() => window.__draws.some(draw => draw.mode === 1 && draw.count === 48));
  await page.screenshot({ path: path.join(evidence, 'colmap-preview.png') });
  const result = {
    ready: await page.evaluate(() => window.ready),
    failures, errors, responses,
    text: await frame.locator('body').innerText(),
    draws: await frame.evaluate(() =>
      [...new Set(window.__draws.map(draw => JSON.stringify(draw)))].map(draw => JSON.parse(draw))
    ),
  };
  fs.writeFileSync(path.join(evidence, 'browser-evidence.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result, null, 2));
  if (failures.length || errors.length || !result.text.includes('3 Images | 1 Cameras')) {
    throw new Error('ColmapUtil browser smoke failed; see browser-evidence.json.');
  }
} finally {
  await browser.close();
}
