import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
const origin = process.env.WEBSITE_URL ?? 'http://127.0.0.1:5199';
const output = new URL('../test-results/scroll/', import.meta.url).pathname;
await mkdir(output, {recursive:true});
const browser = await chromium.launch();
const measurements = [];
try {
  const page = await browser.newPage({viewport:{width:1440,height:900},reducedMotion:'reduce'});
  for (const theme of ['light','dark']) {
    await page.goto(origin+'/docs/quick-start');
    await page.getByLabel('主题',{exact:true}).selectOption(theme);
    for (const [name,fraction] of [['top',0],['middle',.5],['bottom',1]]) {
      await page.evaluate(f => window.scrollTo(0,(document.documentElement.scrollHeight-innerHeight)*f),fraction);
      const state = await page.evaluate(() => {
        const sidebar = document.querySelector('.sidebar').getBoundingClientRect();
        const footer = document.querySelector('footer').getBoundingClientRect();
        return {scrollY,scrollingElement:document.scrollingElement.tagName,sidebarTop:sidebar.top,sidebarBottom:sidebar.bottom,headerBottom:document.querySelector('header').getBoundingClientRect().bottom,footerTop:footer.top,footerBottom:footer.bottom,height:innerHeight};
      });
      assert.equal(state.scrollingElement,'HTML');
      assert.ok(Math.abs(state.sidebarTop-state.headerBottom)<1,`${theme}/${name}: sidebar pushed away`);
      assert.ok(Math.abs(state.sidebarBottom-state.height)<1);
      if (fraction>0) assert.ok(state.scrollY>0);
      if (fraction===1) assert.ok(state.footerTop>=state.headerBottom && state.footerBottom<=state.height+1,'footer visible');
      measurements.push({theme,name,...state});
      await page.screenshot({path:`${output}${theme}-${name}.png`});
    }
  }
  // A short viewport must allow the last nav item to be reached independently.
  for (const width of [1440,900]) {
    await page.setViewportSize({width,height:500});
    await page.goto(origin+'/docs');
    const before = await page.evaluate(() => scrollY);
    const nav = await page.locator('.sidebar').evaluate(el => {
      el.scrollTop=el.scrollHeight;
      const last=el.querySelector('.nav-section:last-child li:last-child').getBoundingClientRect();
      return {overflow:el.scrollHeight>el.clientHeight,scrollTop:el.scrollTop,lastBottom:last.bottom,bottom:el.getBoundingClientRect().bottom};
    });
    assert.ok(nav.overflow && nav.scrollTop>0);
    assert.ok(nav.lastBottom<=nav.bottom);
    assert.equal(await page.evaluate(()=>scrollY),before);
  }
  await page.setViewportSize({width:390,height:844});
  for (const theme of ['light','dark']) {
    await page.goto(origin+'/docs/quick-start');
    await page.getByLabel('主题',{exact:true}).selectOption(theme);
    assert.equal(await page.locator('.sidebar').isVisible(),false);
    await page.locator('.mobile-nav summary').click();
    await page.locator('.mobile-nav').getByRole('link',{name:'第一个工作流',exact:true}).click();
    await page.waitForURL('**/docs/use/workflows');
    await page.evaluate(()=>window.scrollTo(0,document.documentElement.scrollHeight));
    assert.ok(await page.locator('footer').evaluate(el=>el.getBoundingClientRect().bottom<=innerHeight+1));
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await page.screenshot({path:`${output}mobile-${theme}-bottom.png`});
  }
  await writeFile(`${output}measurements.json`,JSON.stringify(measurements,null,2));
  console.log('Scroll regression passed: stable sidebar at top/middle/bottom, native page scroll, visible footer, independently scrollable long navigation, tablet and mobile, both themes.');
} finally {await browser.close();}
