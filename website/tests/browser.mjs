import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import { mkdir, readFile } from 'node:fs/promises';
const origin = process.env.WEBSITE_URL ?? 'http://127.0.0.1:5199';
const { docs, sections } = JSON.parse(await readFile(new URL('../src/content/docs/index.json', import.meta.url), 'utf8'));
const output = new URL('../test-results/', import.meta.url).pathname;
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
const errors = [];
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1, reducedMotion: 'reduce' });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', msg => { if (msg.type() === 'error') errors.push(msg.text()); });
  for (const theme of ['light', 'dark']) {
    await page.goto(origin);
    await page.getByLabel('主题', { exact: true }).selectOption(theme);
    assert.equal(await page.locator('html').getAttribute('data-theme'), theme);
    assert.match(await page.locator('main').innerText(), /就能在画布上用它搭流程/);
    assert.doesNotMatch(await page.locator('main').innerText(), /拖进画布/);
    await page.waitForFunction(t => {
      const img = document.querySelector('.canvas-screenshot img');
      return img?.getAttribute('src') === `/images/workflow-${t}.webp` && img.complete && img.naturalWidth === 2836;
    }, theme);
    assert.equal(await page.locator('.canvas-screenshot img').evaluate(img => img.naturalHeight), 1038);
    assert.equal(await page.locator('.canvas-screenshot').getAttribute('href'), `/images/workflow-${theme}.webp`);
    await page.screenshot({ path: `${output}home-${theme}.png`, fullPage: true });
    await page.getByRole('link', { name: '快速开始 ↗', exact: true }).click();
    await page.waitForURL('**/docs/quick-start');
    assert.equal(await page.locator('html').getAttribute('data-theme'), theme);
    await page.screenshot({ path: `${output}docs-${theme}.png`, fullPage: true });
    await page.reload();
    assert.equal(await page.locator('html').getAttribute('data-theme'), theme);
  }
  for (const theme of ['light', 'dark']) {
    await page.getByLabel('主题', { exact: true }).selectOption(theme);
    for (const route of [...sections.map(s => s.href), ...docs.map(d => d.href)]) {
      await page.goto(origin + route);
      await page.locator('main h1').waitFor();
      assert.equal(await page.locator('main h1').count(), 1, `${route}: one H1`);
      assert.equal(await page.locator('link[rel="canonical"]').getAttribute('href'), `https://hololab.xenotech.studio${route}`);
      const broken = await page.locator('a[href^="#"]').evaluateAll(links => links.filter(a => !document.getElementById(decodeURIComponent(a.hash.slice(1)))).map(a => a.href));
      assert.deepEqual(broken, [], `${route}: missing anchors`);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `${route}: horizontal overflow`);
      assert.equal(await page.locator('html').getAttribute('data-theme'), theme);
      const published = docs.find(d => d.href === route);
      if (published?.source.startsWith('docs/') && published.slug !== 'reference/artifacts') {
        assert.ok((await page.locator('article').innerText()).length > 2000, `${route}: full article rendered`);
        assert.equal(await page.locator('article h2').count(), published.headings.filter(h => h.depth === 2).length);
        await page.screenshot({ path: `${output}${published.slug.split('/').at(-1)}-${theme}.png` });
      }
    }
  }
  await page.goto(origin + '/docs/reference/artifacts');
  assert.doesNotMatch(await page.locator('article').innerText(), /文档整理中/);
  await page.locator('article a[href*="pack-spec"]').click();
  await page.waitForURL('**/docs/reference/pack-spec#arrayed-and-arrayable');
  await page.goto(origin + '/docs/use/results');
  await page.getByRole('link', { name: '产物与工作空间', exact: true }).last().click();
  await page.waitForURL('**/docs/reference/artifacts');
  await page.goBack();
  await page.waitForURL('**/docs/use/results');
  await page.goto(origin + '/docs/use/unknown');
  await page.getByRole('heading', { name: '没有找到这一页' }).waitFor();
  await page.goto(origin + '/docs/quick-start#准备环境');
  await page.waitForFunction(() => document.getElementById('准备环境')?.getBoundingClientRect().top < 180);
  await page.getByLabel('主题', { exact: true }).selectOption('system');
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.waitForFunction(() => document.documentElement.dataset.theme === 'dark');
  await page.emulateMedia({ colorScheme: 'light' });
  await page.waitForFunction(() => document.documentElement.dataset.theme === 'light');
  await page.setViewportSize({ width: 390, height: 844 });
  for (const route of ['/', '/docs/quick-start', '/docs/reference/artifacts']) {
    await page.goto(origin + route);
    await page.locator('main h1').waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `mobile ${route}: horizontal overflow`);
  }
  await page.goto(origin + '/docs/quick-start');
  await page.locator('.mobile-nav summary').click();
  await page.locator('.mobile-nav').getByRole('link', { name: '第一个工作流', exact: true }).click();
  await page.waitForURL('**/docs/use/workflows');
  await page.screenshot({ path: `${output}docs-mobile.png`, fullPage: true });
  await page.goto(origin);
  await page.getByLabel('主题', { exact: true }).selectOption('system');
  for (const theme of ['light', 'dark']) {
    await page.emulateMedia({colorScheme: theme});
    await page.waitForFunction(t => {
      const img = document.querySelector('.canvas-screenshot img');
      return img?.src.endsWith(`workflow-${t}.webp`) && img.complete && img.naturalWidth === 2836;
    }, theme);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({path: `${output}home-mobile-${theme}.png`, fullPage: true});
  }
  await page.screenshot({ path: `${output}home-mobile.png`, fullPage: true });
  assert.deepEqual(errors, [], 'browser console/page errors');
  console.log(`Browser checks passed: ${docs.length + sections.length} document routes in both themes, 404, anchors, links, history, persistent/system themes, mobile navigation and overflow; desktop and mobile screenshots in ${output}`);
} finally { await browser.close(); }
