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
    assert.equal(excerpt.resource.algorithm_name, 'image-undistort');
    assert.deepEqual(Object.keys(excerpt), ['kind', 'ref', 'resource', 'related']);
    const skillExcerpt = (await page.locator('code.language-bash').textContent()).trim();
    assert.ok((await readFile('../skill/SKILL.md', 'utf8')).includes(skillExcerpt));
    assert.equal(await page.locator('article .no-flops-only').isVisible(), !flops);
    // Exercise direct picture classes on the real shared illustration.
    const picture = page.locator('article .doc-screenshot picture');
    await picture.evaluate(el => el.classList.add('flops-only'));
    assert.equal(await picture.isVisible(), flops);
    await picture.evaluate(el => { el.classList.remove('flops-only'); el.classList.add('no-flops-only'); });
    assert.equal(await picture.isVisible(), !flops);
    await picture.evaluate(el => el.classList.remove('no-flops-only'));
    for (const theme of ['light', 'dark']) {
      await page.locator('.theme-control').selectOption(theme);
      await page.waitForFunction(theme => { const img = document.querySelector('article .doc-screenshot img'); return img.complete && img.naturalWidth > 0 && img.currentSrc.endsWith(`-${theme}.webp`); }, theme);
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
