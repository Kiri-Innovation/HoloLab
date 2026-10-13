import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
const origin = process.env.WEBSITE_URL ?? 'http://127.0.0.1:5199';
const output = new URL('../test-results/introduction/', import.meta.url).pathname;
await mkdir(output, { recursive: true });
const browser = await chromium.launch();
const measurements = [];
try {
  const page = await browser.newPage({ reducedMotion: 'reduce' });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  for (const [device, width, height] of [['desktop',1440,900],['mobile',390,844]]) {
    await page.setViewportSize({ width, height });
    for (const theme of ['light','dark']) {
      await page.goto(origin + '/docs/introduction');
      await page.getByLabel('主题', { exact:true }).selectOption(theme);
      await page.waitForFunction(t => document.documentElement.dataset.theme === t, theme);
      assert.equal(await page.locator('article h1').innerText(), '介绍 HoloLab');
      assert.doesNotMatch(await page.locator('article').innerText(), /\*\*/);
      assert.equal(await page.locator('article figure').count(), 3);
      assert.equal(await page.locator('article svg path').count(), 10);
      assert.equal(await page.locator('.concept-person strong svg').getAttribute('aria-hidden'), 'true');
      assert.deepEqual((await page.locator('.sidebar .doc-link').allTextContents()).slice(0,2), ['介绍 HoloLab','快速开始']);
      const state = await page.locator('article').evaluate(article => {
        const rect = article.getBoundingClientRect();
        const paths = [...article.querySelectorAll('svg path')];
        return {
          articleHeight:rect.height, viewportWidth:innerWidth, pageWidth:document.documentElement.scrollWidth,
          direction:getComputedStyle(article.querySelector('.concept-flow')).flexDirection,
          strokes:paths.map(p => getComputedStyle(p).stroke),
          arrowColors:paths.map(p => getComputedStyle(p.closest('svg')).color),
          diagramBackground:getComputedStyle(article.querySelector('figure')).backgroundColor,
          diagramsFit:[...article.querySelectorAll('figure, .concept-card, .concept-person, svg')].every(el => {
            const r=el.getBoundingClientRect();
            return r.width>0 && r.height>0 && r.left>=rect.left && r.right<=rect.right+1 && el.scrollWidth<=el.clientWidth+1;
          }),
        };
      });
      assert.equal(state.pageWidth,width);
      assert.ok(state.diagramsFit, 'diagram content fits without horizontal scrolling');
      assert.equal(state.direction,device==='mobile'?'column':'row');
      assert.ok(state.strokes.every(c=>c!=='none' && c!=='rgba(0, 0, 0, 0)'), 'SVG arrows are painted');
      assert.deepEqual(state.strokes,state.arrowColors,'SVG uses currentColor');
      // The guided introduction uses eight short sections with room between paragraphs.
      if (device==='desktop') assert.ok(state.articleHeight<=height*2.5,'guide stays within two and a half desktop screens');
      measurements.push({device,theme,...state});
      await page.screenshot({ path:`${output}${device}-${theme}.png`, fullPage:true });
      await page.locator('article').getByRole('link',{name:'快速开始 →',exact:true}).click();
      await page.waitForURL('**/docs/quick-start');
      assert.match(await page.locator('.page-turn a').first().innerText(), /介绍 HoloLab/);
    }
  }
  assert.deepEqual(errors,[]);
  assert.notEqual(measurements[0].diagramBackground,measurements[1].diagramBackground,'diagrams follow theme colors');
  await writeFile(`${output}measurements.json`,JSON.stringify(measurements,null,2));
  console.log('Introduction passed: navigation order, sanitized SVG rendering, both themes, responsive diagrams, no overflow, next/previous links.');
} finally { await browser.close(); }
