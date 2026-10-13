import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
const origin = process.env.WEBSITE_URL ?? 'http://127.0.0.1:5199';
const output = new URL('../test-results/scroll/', import.meta.url).pathname;
await mkdir(output, {recursive:true});
const browser = await chromium.launch();
const measurements = [];
async function measure(page) {
  return page.evaluate(() => {
    const sidebar = document.querySelector('.sidebar').getBoundingClientRect();
    const footer = document.querySelector('footer').getBoundingClientRect();
    return {scrollY,scrollingElement:document.scrollingElement.tagName,sidebarTop:sidebar.top,sidebarBottom:sidebar.bottom,headerBottom:document.querySelector('header').getBoundingClientRect().bottom,footerTop:footer.top,footerBottom:footer.bottom,footerLeft:footer.left,footerWidth:footer.width,viewportWidth:document.documentElement.clientWidth,pageWidth:document.documentElement.scrollWidth,height:innerHeight};
  });
}
function assertFullWidth(state) {
  assert.ok(Math.abs(state.footerLeft)<1,'footer reaches left viewport edge');
  assert.ok(Math.abs(state.footerWidth-state.viewportWidth)<1,'footer spans viewport');
  assert.equal(state.pageWidth,state.viewportWidth,'no horizontal page overflow');
  assert.equal(state.scrollingElement,'HTML','native page scrolling');
}
try {
  const page = await browser.newPage({viewport:{width:1440,height:900},reducedMotion:'reduce'});
  for (const theme of ['light','dark']) {
    await page.goto(origin+'/docs/quick-start');
    await page.getByLabel('主题',{exact:true}).selectOption(theme);
    for (const [name,fraction] of [['top',0],['middle',.5],['bottom',1]]) {
      await page.evaluate(f => window.scrollTo(0,(document.documentElement.scrollHeight-innerHeight)*f),fraction);
      const state = await measure(page);
      assertFullWidth(state);
      assert.ok(Math.abs(state.sidebarTop-state.headerBottom)<1,`${theme}/${name}: sidebar pushed away`);
      if (fraction>0) assert.ok(state.scrollY>0);
      if (fraction===1) {
        assert.ok(state.footerTop>=state.headerBottom && Math.abs(state.footerBottom-state.height)<1,'footer visible at page bottom');
        assert.ok(state.sidebarBottom<=state.footerTop+1,'sidebar does not overlap footer');
        // Test actual hit targets under both navigation rails, not just dimensions.
        assert.ok(await page.evaluate(() => {
          const footer=document.querySelector('footer');
          const y=footer.getBoundingClientRect().top+10;
          return ['.sidebar','.toc'].every(selector => {
            const rail=document.querySelector(selector).getBoundingClientRect();
            return footer.contains(document.elementFromPoint(rail.left+10,y));
          });
        }),'footer visible beneath both rails');
      }
      measurements.push({theme,name,...state});
      await page.screenshot({path:`${output}${theme}-${name}.png`});
    }
  }
  // Cover short pages, constrained navigation height, tablet and wide desktops.
  for (const width of [1920,1440,900,701]) {
    await page.setViewportSize({width,height:500});
    for (const route of ['/docs','/docs/quick-start']) {
      await page.goto(origin+route);
      await page.evaluate(()=>window.scrollTo(0,document.documentElement.scrollHeight));
      const state = await measure(page);
      assertFullWidth(state);
      assert.equal(state.sidebarTop,state.headerBottom);
      assert.ok(state.sidebarBottom<=state.footerTop+1,'short viewport: no footer overlap');
      const before = state.scrollY;
      const nav = await page.locator('.sidebar').evaluate(el => {
        el.scrollTop=el.scrollHeight;
        const last=el.querySelector('.nav-section:last-child li:last-child').getBoundingClientRect();
        return {overflow:el.scrollHeight>el.clientHeight,scrollTop:el.scrollTop,lastBottom:last.bottom,bottom:el.getBoundingClientRect().bottom,lastVisible:el.contains(document.elementFromPoint(last.left+5,last.top+last.height/2))};
      });
      assert.ok(nav.overflow && nav.scrollTop>0);
      assert.ok(nav.lastBottom<=nav.bottom && nav.lastVisible,'last navigation link reachable above footer');
      assert.equal(await page.evaluate(()=>scrollY),before);
      measurements.push({name:'short-viewport-bottom',route,...state});
    }
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
    const state=await measure(page);
    assertFullWidth(state);
    assert.ok(Math.abs(state.footerBottom-state.height)<1);
    measurements.push({theme,name:'mobile-bottom',...state});
    await page.screenshot({path:`${output}mobile-${theme}-bottom.png`});
  }
  await writeFile(`${output}measurements.json`,JSON.stringify(measurements,null,2));
  console.log('Scroll regression passed: stable sidebar, full-width footer without navigation overlap, native page scroll, independently scrollable long navigation, wide desktop, tablet and mobile, both themes.');
} finally {await browser.close();}
