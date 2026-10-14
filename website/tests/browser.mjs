import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
const origin = process.env.WEBSITE_URL ?? 'http://127.0.0.1:5199';
const { docs, sections } = JSON.parse(await readFile(new URL('../src/content/docs/index.json', import.meta.url), 'utf8'));
const output = new URL('../test-results/i18n/', import.meta.url).pathname;
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
const errors = [], measurements = [];
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'en-US', reducedMotion: 'reduce' });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  const noOverflow = async route => {
    const size = await page.evaluate(() => ({ width: innerWidth, content: document.documentElement.scrollWidth }));
    assert.ok(size.content <= size.width, `${route}: ${JSON.stringify(size)}`);
  };
  for (const locale of ['en', 'zh-CN']) {
    const prefix = locale === 'en' ? '' : '/zh-CN';
    for (const theme of ['light', 'dark']) {
      for (const [name, route] of [['home', `${prefix}/`], ['quick-start', `${prefix}/docs/quick-start`], ['introduction', `${prefix}/docs/introduction`], ['run-records', `${prefix}/docs/use/run-records`]]) {
        await page.goto(origin + route);
        await page.locator('.theme-control').selectOption(theme);
        await page.locator('main h1').waitFor();
        await noOverflow(route);
        assert.equal(await page.locator('html').getAttribute('lang'), locale);
        assert.equal(await page.locator('html').getAttribute('data-theme'), theme);
        await page.screenshot({ path: `${output}${locale}-${name}-${theme}.png`, fullPage: true });
      }
    }
    const routes = [...sections.map(s => prefix + s.href), ...docs.filter(d => d.locale === locale).map(d => d.href)];
    for (const route of routes) {
      await page.goto(origin + route);
      await page.locator('main h1').waitFor();
      assert.equal(await page.locator('main h1').count(), 1);
      assert.equal(await page.locator('link[rel="canonical"]').getAttribute('href'), `https://hololab.xenotech.studio${route}`);
      assert.equal(await page.locator('link[hreflang="en"]').getAttribute('href'), `https://hololab.xenotech.studio${route.replace(/^\/zh-CN/, '')}`);
      assert.equal(await page.locator('link[hreflang="zh-CN"]').getAttribute('href'), `https://hololab.xenotech.studio/zh-CN${route.replace(/^\/zh-CN/, '')}`);
      assert.equal(await page.locator('meta[property="og:locale"]').getAttribute('content'), locale === 'en' ? 'en_US' : 'zh_CN');
      const broken = await page.locator('a[href^="#"]').evaluateAll(links => links.filter(a => !document.getElementById(decodeURIComponent(a.hash.slice(1)))).map(a => a.href));
      assert.deepEqual(broken, [], route);
      if (locale === 'en') assert.doesNotMatch(await page.locator('.sidebar').innerText(), /[\u4e00-\u9fff]/);
      await noOverflow(route);
    }
    for (const width of [390, 320]) {
      await page.setViewportSize({ width, height: 844 });
      for (const route of [`${prefix}/`, ...routes]) {
        await page.goto(origin + route);
        await page.locator('main h1').waitFor();
        await noOverflow(route);
      }
      for (const theme of ['light', 'dark']) {
        for (const [name, route] of [['home', `${prefix}/`], ['introduction', `${prefix}/docs/introduction`]]) {
          await page.goto(origin + route);
          await page.locator('.theme-control').selectOption(theme);
          await page.screenshot({ path: `${output}${locale}-mobile-${width}-${name}-${theme}.png`, fullPage: true });
          if (name === 'introduction' && width === 390) await page.locator('figure.concept-diagram').nth(1).screenshot({ path: `${output}${locale}-mobile-diagram-${theme}.png` });
        }
      }
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
  }
  // Switching preserves the article, query string, corresponding heading and theme.
  await page.goto(origin + '/docs/quick-start?from=test#prerequisites');
  await page.locator('.theme-control').selectOption('dark');
  await page.locator('.language-control').selectOption('zh-CN');
  await page.waitForURL('**/zh-CN/docs/quick-start?from=test#*');
  assert.equal(decodeURIComponent(new URL(page.url()).hash), '#准备环境');
  assert.equal(await page.locator('main h1').innerText(), '安装和运行');
  assert.equal(await page.locator('html').getAttribute('data-theme'), 'dark');
  assert.equal(await page.evaluate(() => localStorage.getItem('hololab.website.locale')), 'zh-CN');
  await page.goto(origin + '/');
  await page.waitForURL('**/zh-CN/');
  await page.goto(origin + '/docs/quick-start');
  assert.equal(await page.locator('main h1').innerText(), 'Install and run', 'explicit URL wins over preference');
  await page.locator('.language-control').selectOption('zh-CN');
  await page.waitForURL('**/zh-CN/docs/quick-start');
  await page.locator('.language-control').selectOption('en');
  await page.waitForURL('**/docs/quick-start');
  await page.goto(origin + '/');
  assert.equal(new URL(page.url()).pathname, '/');
  await page.goto(origin + '/zh-CN/docs/use/results');
  await page.locator('article a[href*="reference/artifacts"]').click();
  await page.waitForURL('**/zh-CN/docs/reference/artifacts');
  assert.equal(await page.locator('article').getAttribute('lang'), 'en');
  assert.match(await page.locator('.reference-note').first().innerText(), /英文原文/);
  await page.locator('.theme-control').selectOption('system');
  for (const theme of ['light', 'dark']) {
    await page.emulateMedia({ colorScheme: theme });
    await page.waitForFunction(t => document.documentElement.dataset.theme === t, theme);
  }
  for (const locale of ['en', 'zh-CN']) {
    const prefix = locale === 'en' ? '' : '/zh-CN';
    await page.goto(origin + `${prefix}/docs/quick-start`);
    measurements.push(await page.evaluate(() => ({ url: location.pathname, lang: document.documentElement.lang, title: document.title, canonical: document.querySelector('link[rel="canonical"]').href, alternates: [...document.querySelectorAll('link[hreflang]')].map(a => [a.hreflang, a.href]), ogLocale: document.querySelector('meta[property="og:locale"]').content })));
    await page.goto(origin + `${prefix}/docs/not-real`);
    assert.match(await page.locator('main h1').innerText(), locale === 'en' ? /Page not found/ : /没有找到/);
  }
  const chinese = await browser.newContext({ locale: 'zh-CN' });
  const firstVisit = await chinese.newPage();
  await firstVisit.goto(origin + '/');
  await firstVisit.waitForURL('**/zh-CN/');
  await chinese.close();
  const noStorage = await browser.newContext({ locale: 'en-US' });
  await noStorage.addInitScript(() => { Object.defineProperty(window, 'localStorage', { get() { throw new Error('blocked'); } }); });
  const privatePage = await noStorage.newPage();
  await privatePage.goto(origin + '/docs/quick-start');
  await privatePage.locator('.language-control').selectOption('zh-CN');
  await privatePage.waitForURL('**/zh-CN/docs/quick-start');
  await noStorage.close();
  assert.deepEqual(errors, []);
  await writeFile(`${output}metadata.json`, JSON.stringify(measurements, null, 2));
  console.log(`Passed: 24 articles + 6 indexes, desktop/390/320px, both locales and themes, switching, preferences, storage denial, links, metadata. Screenshots: ${output}`);
} finally { await browser.close(); }
