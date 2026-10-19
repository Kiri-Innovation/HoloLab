import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import { mkdir, readFile } from 'node:fs/promises';
const origin = process.env.WEBSITE_URL ?? 'http://127.0.0.1:5199';
const output = new URL('../test-results/doc-areas/', import.meta.url).pathname;
const {docs} = JSON.parse(await readFile(new URL('../src/content/docs/index.json', import.meta.url), 'utf8'));
await mkdir(output, {recursive: true});
const browser = await chromium.launch();
try {
  for (const locale of ['en', 'zh-CN']) {
    const prefix = locale === 'en' ? '' : '/zh-CN';
    const page = await browser.newPage({viewport: {width: 1440, height: 1100}});
    const areas = page.locator('.doc-areas');
    const links = areas.locator('a');
    const check = async (section) => {
      assert.equal(await links.count(), 2);
      const expected = docs.filter(d => d.locale === locale && d.section === section).map(d => d.href).sort();
      assert.deepEqual((await page.locator('.sidebar .doc-link').evaluateAll(es => es.map(e => e.getAttribute('href')))).sort(), expected);
      assert.equal(await areas.locator('a[aria-current="page"]').getAttribute('href'), prefix + (section === 'learn' ? '/docs' : '/docs/contributor'));
      assert.equal(await page.locator('html').getAttribute('lang'), locale);
      const header = await page.locator('.header').boundingBox(), tabs = await areas.boundingBox();
      assert.ok(Math.abs(tabs.y - header.y - header.height) < 1, 'tabs directly below header');
      assert.equal(tabs.width, 1440);
    };
    for (const theme of ['light', 'dark']) {
      await page.goto(origin + prefix + '/docs');
      await page.locator('.theme-control').selectOption(theme);
      for (const [index, section] of [[1, 'contributor'], [0, 'learn']]) {
        await links.nth(index).click();
        await page.waitForURL(origin + prefix + (index ? '/docs/contributor' : '/docs'));
        await check(section);
        await page.screenshot({path: `${output}${locale}-${theme}-${section}.png`});
        await page.screenshot({path: `${output}${locale}-${theme}-${section}-tabs.png`, clip: {x: 0, y: 0, width: 1440, height: 170}});
      }
      // Article membership is independent of its legacy /build URL prefix.
      for (const [slug, section] of [['build/writing-a-pack', 'learn'], ['build/architecture', 'contributor']]) {
        await page.goto(origin + prefix + '/docs/' + slug);
        await check(section);
      }
    }
    await page.setViewportSize({width: 360, height: 800});
    await page.goto(origin + prefix + '/docs');
    await links.nth(1).click();
    assert.equal(new URL(page.url()).pathname, prefix + '/docs/contributor');
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.locator('.mobile-nav summary').click();
    assert.equal(await page.locator('.mobile-nav .doc-link').count(), 2);
    await page.screenshot({path: `${output}${locale}-mobile.png`});
    await page.goto(origin + prefix + '/');
    assert.equal(await areas.count(), 0, 'home has no documentation-area tabs');
    await page.close();
  }
  const page = await browser.newPage();
  await page.goto(origin + '/docs/contributor');
  await page.locator('.language-control').selectOption('zh-CN');
  await page.waitForURL(origin + '/zh-CN/docs/contributor');
  assert.equal(await page.locator('.doc-areas a[aria-current="page"]').textContent(), '贡献者文档');
  console.log('PASS: header/tab geometry, two-way switching, section-only sidebar, legacy article membership, language retention/switching, light/dark, mobile, and home exclusion.');
} finally { await browser.close(); }
