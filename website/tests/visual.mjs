// Read-only product reference + local website parity checks and comparison boards.
// Product credentials, workflow data and screenshots remain in ignored test-results/.
import { chromium } from '@playwright/test';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
const productURL = process.env.PRODUCT_URL;
if (!productURL) throw new Error('Set PRODUCT_URL to the read-only product reference URL.');
const websiteURL = process.env.WEBSITE_URL ?? 'http://127.0.0.1:5200';
const output = new URL('../test-results/visual/', import.meta.url).pathname;
await mkdir(output, { recursive: true });
const browser = await chromium.launch();
const metrics = [];
async function style(page, selector) {
  return page.locator(selector).first().evaluate(el => {
    const s = getComputedStyle(el);
    return Object.fromEntries(['fontFamily','fontSize','fontWeight','lineHeight','color','backgroundColor','borderColor','borderWidth','borderRadius','height','padding','boxShadow'].map(key => [key,s[key]]));
  });
}
async function platformFonts(context, page, selector) {
  const cdp = await context.newCDPSession(page);
  await cdp.send('DOM.enable'); await cdp.send('CSS.enable');
  const {root} = await cdp.send('DOM.getDocument');
  const {nodeId} = await cdp.send('DOM.querySelector', {nodeId:root.nodeId,selector});
  const {fonts} = await cdp.send('CSS.getPlatformFontsForNode', {nodeId});
  await cdp.detach();
  return fonts.map(({familyName,isCustomFont}) => ({familyName,isCustomFont}));
}
try {
  for (const theme of ['light','dark']) {
    const productContext = await browser.newContext({viewport:{width:1440,height:1000},colorScheme:theme,reducedMotion:'reduce',serviceWorkers:'block'});
    const blocked = [];
    await productContext.route('**/*', route => {
      if (!['GET','HEAD','OPTIONS'].includes(route.request().method())) {
        blocked.push(route.request().method()); return route.abort();
      }
      return route.continue();
    });
    await productContext.addInitScript(t => localStorage.setItem('hololab.theme', t), theme);
    const product = await productContext.newPage();
    // Only visit Gallery; do not open editable canvases or click product actions.
    await product.goto(productURL, {waitUntil:'networkidle'});
    await product.locator('.hl-topbar').waitFor();
    await product.screenshot({path:`${output}product-${theme}.png`});
    const productMetrics = {
      body:await style(product,'body'), header:await style(product,'.hl-topbar'),
      primary:await style(product,'.hl-topbar > button:last-child'),
      brand:await style(product,'.hl-topbar > div:first-child'),
      fonts:await platformFonts(productContext,product,'.hl-topbar > div:first-child'),
    };
    assert.deepEqual(blocked, [], 'Gallery unexpectedly attempted a write; request was blocked');
    const context = await browser.newContext({viewport:{width:1440,height:1000},colorScheme:theme,reducedMotion:'reduce'});
    await context.addInitScript(t => localStorage.setItem('hololab.website.theme',t), theme);
    const page = await context.newPage();
    await page.goto(websiteURL);
    await page.locator('.hero').waitFor();
    await page.screenshot({path:`${output}home-${theme}.png`});
    const siteMetrics = {
      body:await style(page,'body'), header:await style(page,'.header'),
      surface:await style(page,'.header'), primary:await style(page,'.actions .primary'),
      brand:await style(page,'.header .brand'),
      fonts:await platformFonts(context,page,'.header .brand'),
    };
    for (const key of ['fontFamily','backgroundColor','color']) assert.equal(siteMetrics.body[key], productMetrics.body[key], `body ${theme} ${key}`);
    assert.equal(siteMetrics.header.height,productMetrics.header.height, 'header geometry');
    assert.equal(siteMetrics.surface.backgroundColor,productMetrics.header.backgroundColor, 'header surface');
    for (const key of ['fontSize','fontWeight','lineHeight','fontFamily']) assert.equal(siteMetrics.brand[key],productMetrics.brand[key],`brand ${key}`);
    for (const key of ['fontSize','fontWeight','fontFamily','lineHeight','backgroundColor','borderRadius','color','borderWidth','padding']) assert.equal(siteMetrics.primary[key],productMetrics.primary[key],`primary ${theme} ${key}`);
    assert.equal(siteMetrics.primary.height,'32px','site uses product large action size');
    assert.deepEqual(siteMetrics.fonts,productMetrics.fonts,'actual rendered brand fonts');
    await page.getByLabel('主题',{exact:true}).focus();
    await page.getByRole('tooltip').waitFor({state:'visible'});
    assert.notEqual((await style(page,'.theme-control select')).boxShadow,'none');
    await page.mouse.click(1000,300);
    await page.goto(websiteURL+'/docs/quick-start');
    await page.locator('article pre').first().waitFor();
    await page.screenshot({path:`${output}docs-${theme}.png`});
    assert.equal((await style(page,'.sidebar')).backgroundColor,productMetrics.header.backgroundColor);
    const code = await style(page,'.prose pre');
    assert.equal(code.borderRadius,'4px');
    assert.equal(code.backgroundColor,theme==='light'?'rgb(250, 250, 250)':'rgb(37, 37, 38)');
    metrics.push({theme,product:productMetrics,site:siteMetrics,code,blockedWrites:blocked.length});
    await context.close(); await productContext.close();
    // Render a report page with two native-size screenshots; no image manipulation.
    for (const name of ['home','docs']) {
      const left = (await readFile(`${output}product-${theme}.png`)).toString('base64');
      const right = (await readFile(`${output}${name}-${theme}.png`)).toString('base64');
      const html = `<!doctype html><meta charset="utf-8"><title>HoloLab ${theme}: product / ${name}</title><style>body{margin:0;background:${theme==='light'?'#f6f6f6':'#181818'};color:${theme==='light'?'#262626':'#ccc'};font:16px Arial,sans-serif}main{display:flex}section{width:1440px;flex:none}h1{font-size:16px;font-weight:600;height:48px;line-height:48px;margin:0;padding:0 24px;border-bottom:1px solid ${theme==='light'?'#d9d9d9':'#3c3c3c'}}img{display:block;width:1440px;height:1000px}</style><main><section><h1>产品 · ${theme}</h1><img src="data:image/png;base64,${left}"></section><section><h1>官网 ${name==='home'?'首页':'文档'} · ${theme}</h1><img src="data:image/png;base64,${right}"></section></main>`;
      await writeFile(`${output}compare-${name}-${theme}.html`,html);
      const board = await browser.newPage({viewport:{width:2880,height:1049}});
      await board.setContent(html);
      await board.locator('img').evaluateAll(images=>Promise.all(images.map(img=>img.decode())));
      await board.screenshot({path:`${output}compare-${name}-${theme}.png`});
      await board.close();
    }
  }
  await writeFile(`${output}metrics.json`,JSON.stringify(metrics,null,2));
  console.log('Visual parity passed in light/dark: live fonts, colors, header, buttons, sidebar, code, focus and tooltip; four comparison boards. No product writes.');
} finally { await browser.close(); }
