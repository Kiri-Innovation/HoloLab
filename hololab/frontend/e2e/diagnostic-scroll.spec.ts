import {test,expect} from '@playwright/test';
import fs from 'node:fs';
test.use({video:'on',viewport:{width:1300,height:900}});
test('diagnostic iframe and text output scroll only while focused',async({page})=>{
 const assets=process.env.DIAGNOSTIC_ASSETS;
 await page.route('**/pack-assets/**',async route=>{
  const name=new URL(route.request().url()).pathname.split('/').pop()!;
  const fallback='<html><head></head><body style="margin:0"><div style="height:850px">Training diagnostic scroll fixture</div></body></html>';
  await route.fulfill({contentType:name.endsWith('.css')?'text/css':name.endsWith('.js')?'text/javascript':'text/html',body:assets?fs.readFileSync(`${assets}/${name==='viewer.html'?'index.html':name}`):fallback});
 });
 await page.route('**/fixture-ssim/*',route=>route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="320" height="160"><rect width="320" height="160" fill="#273544"/><text x="15" y="80" fill="white">SSIM test image</text></svg>'}));
 await page.route('**/fixture-text',route=>route.fulfill({body:Array.from({length:150},(_,i)=>`Diagnostic line ${i}`).join('\n')}));
 await page.route('**/scroll-test?*',route=>route.fulfill({contentType:'text/html',body:`<!doctype html><html><body style="margin:0"><div id="root"></div><script type="module">import RefreshRuntime from '/@react-refresh';RefreshRuntime.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;window.__vite_plugin_react_preamble_installed__=true;</script><script type="module" src="/e2e/fixtures/viewer-focus.tsx"></script></body></html>`}));
 await page.goto('/scroll-test?scroll=1');
 const iframe=page.locator('iframe');await expect(iframe).toHaveCount(1);
 // Force the same overflow boundary as a diagnostic taller than the host cap.
 await iframe.contentFrame().locator('body').evaluate(el=>el.style.minHeight='850px');
 const viewport=page.locator('.react-flow__viewport'),transform=()=>viewport.evaluate(el=>getComputedStyle(el).transform);
 fs.mkdirSync('/tmp/diagnostic-scroll',{recursive:true});
 for(const theme of ['light','dark']){
  await page.evaluate(t=>document.documentElement.dataset.theme=t,theme);
  for(const id of ['custom','text']){
   const gate=page.locator(`.react-flow__node[data-id="${id}"] [data-viewer-interaction]`);
   const scroll=()=>id==='custom'?iframe.contentFrame().locator('html').evaluate(()=>document.scrollingElement!.scrollTop):gate.locator(':scope > div').first().evaluate(el=>el.scrollTop);
   await expect(gate).toHaveAttribute('data-viewer-interaction','inactive');
   if(id==='custom')await expect(iframe.contentFrame().locator('html')).toHaveCSS('overflow','hidden');
   const offset=await scroll();await gate.hover();const before=await transform();await page.mouse.wheel(0,100);await expect.poll(transform).not.toBe(before);expect(await scroll()).toBe(offset);
   await page.screenshot({path:`/tmp/diagnostic-scroll/${theme}-${id}-inactive.png`});
   await gate.click();await expect(gate).toHaveAttribute('data-viewer-interaction','active');
   if(id==='custom')await expect(iframe.contentFrame().locator('#hololab-host-scroll-gate')).toHaveCount(0);
   await gate.hover();const focused=await transform();await page.mouse.wheel(0,120);await expect.poll(scroll).toBeGreaterThan(offset);expect(await transform()).toBe(focused);
   await page.screenshot({path:`/tmp/diagnostic-scroll/${theme}-${id}-active.png`});
   await page.mouse.click(20,800);await expect(gate).toHaveAttribute('data-viewer-interaction','inactive');
  }
 }
 if(assets){
  const gate=page.locator('.react-flow__node[data-id="custom"] [data-viewer-interaction]');await gate.click();
  await iframe.contentFrame().locator('body').evaluate(()=>window.scrollTo(0,0));
  await expect(iframe.contentFrame().locator('#pager')).toHaveText('0 / 0');
  await expect(iframe.contentFrame().locator('#progress-text')).toContainText('50');
  await page.goto('/scroll-test?scroll=1&images=1');
  await gate.click();
  await expect(iframe.contentFrame().locator('#pager')).toHaveText('2 / 2');
  await expect(iframe.contentFrame().locator('#stage img')).toBeVisible();
  await iframe.contentFrame().getByRole('button',{name:'上一张',exact:true}).click();await expect(iframe.contentFrame().locator('#pager')).toHaveText('1 / 2');
  await iframe.contentFrame().getByRole('button',{name:'跳到最新'}).click();await expect(iframe.contentFrame().locator('#pager')).toHaveText('2 / 2');
  for(const theme of ['light','dark']) {await page.evaluate(t=>document.documentElement.dataset.theme=t,theme);await page.screenshot({path:`/tmp/diagnostic-scroll/${theme}-real-diagnostic.png`});}

 }
});
