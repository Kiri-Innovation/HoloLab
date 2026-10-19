import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import { mkdir, readFile } from 'node:fs/promises';
const origin = process.env.WEBSITE_URL ?? 'http://127.0.0.1:5200';
const output = 'test-results/browser-environment';
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ args: ['--no-proxy-server'] });
try {
  for (const language of ['en', 'zh-CN']) for (const mode of ['ordinary', 'empty-bridge', 'flops', 'mobile']) {
    const flops = mode === 'flops' || mode === 'mobile';
    const page = await browser.newPage({ viewport: mode === 'mobile' ? { width: 390, height: 844 } : { width: 1280, height: 1000 } });
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
    // Exercise the reverse class without adding irrelevant copy to the article.
    await page.evaluate(() => { const el = document.createElement('div'); el.className = 'no-flops-only'; el.textContent = 'Ordinary browser'; document.querySelector('article').append(el); });
    assert.equal(await page.locator('.no-flops-only').isVisible(), !flops);
    await page.locator('.no-flops-only').evaluate(el => el.remove());
    for (const theme of ['light', 'dark']) {
      await page.locator('.theme-control').selectOption(theme);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
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
