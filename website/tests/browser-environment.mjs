import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import { mkdir, readFile } from 'node:fs/promises';
const origin = process.env.WEBSITE_URL ?? 'http://127.0.0.1:5200';
const output = 'test-results/browser-environment';
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ args: ['--no-proxy-server'] });
try {
  for (const language of ['en', 'zh-CN']) for (const mode of ['ordinary', 'empty-bridge', 'flops', 'mobile', 'mobile-ordinary']) {
    const flops = mode === 'flops' || mode === 'mobile';
    const page = await browser.newPage({ viewport: mode.startsWith('mobile') ? { width: 390, height: 844 } : { width: 1280, height: 1000 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(({ flops, mode }) => {
      if (flops) window.flops = { version: 1, insertReference: async () => ({ success: true }) };
      else if (mode === 'empty-bridge') window.flops = {};
    }, { flops, mode });
    await page.goto(`${origin}${language === 'en' ? '' : '/zh-CN'}/docs/use/agent-oriented`);
    await page.locator('article').waitFor();
    assert.equal(await page.locator('html').getAttribute('data-flops'), flops ? '1' : '0');
    assert.equal(await page.locator('article .flops-only').isVisible(), flops);
    assert.match(await page.locator('article .flops-only').textContent(), /hololab.reference-snapshot\/v1/);
    assert.equal(await page.locator('article .flops-only code').count(), 1);
    const excerpt = JSON.parse(await page.locator('code.language-json').textContent());
    assert.equal(excerpt.resource.state, 'done');
    assert.equal(excerpt.resource.algorithm_name, 'community/image-undistort');
    assert.deepEqual(Object.keys(excerpt), ['kind', 'ref', 'resource', 'related']);
    assert.equal(await page.locator('code.language-bash').count(), 0);
    assert.equal(await page.locator('article .no-flops-only').isVisible(), !flops);
    assert.equal(await page.locator('article .no-flops-only figure').isVisible(), !flops);
    assert.equal(await page.locator('article .no-flops-only .reference-label').textContent(), language === 'en' ? 'Copy reference info' : '复制引用信息');
    assert.equal(await page.locator('article .flops-only .reference-label').textContent(), language === 'en' ? 'Reference to Flops' : '引用到 Flops');
    assert.equal(await page.locator('article .flops-only figure').isVisible(), flops);
    assert.equal((await page.locator('article .no-flops-only code.language-text').textContent()).trim(), 'hololab://graph-node/<workflow-id>/fx  # frame-extraction');
    for (const theme of ['light', 'dark']) {
      await page.locator('.theme-control').selectOption(theme);
      const selector = flops ? 'article .flops-only img' : 'article .no-flops-only img';
      await page.locator(selector).scrollIntoViewIfNeeded();
      await page.waitForFunction(({theme, selector}) => { const img = document.querySelector(selector); return img.complete && img.naturalWidth > 0 && img.currentSrc.endsWith(`-${theme}.webp`); }, {theme, selector});
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await page.locator(flops ? 'article .flops-only' : 'article .no-flops-only').screenshot({ path: `${output}/${language}-${mode}-${theme}-reference.png` });
      await page.screenshot({ path: `${output}/${language}-${mode}-${theme}.png`, fullPage: true });
    }
    assert.deepEqual(errors, []);
    await page.close();
  }
  const data = JSON.parse(await readFile('src/content/docs/index.json', 'utf8'));
  for (const doc of data.docs.filter(d => d.slug === 'use/agent-oriented')) {
    assert.match(doc.html, /class="flops-only"/);
    const html = await readFile(`dist${doc.href}/index.html`, 'utf8');
    assert.ok(html.includes('<div id="root"></div>'));
    assert.ok(!html.includes('hololab.reference-snapshot/v1'));
  }
  console.log('Passed: EN/ZH, ordinary/empty bridge/Flops/mobile, light/dark, reverse class, Markdown, no overflow or browser errors. Static HTML remains a head + SPA shell; body is in generated content.');
} finally { await browser.close(); }
