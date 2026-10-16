import {test,expect} from '@playwright/test';
import {mkdirSync} from 'node:fs';
for(const mode of ['default','custom','hidden','legacy','empty','unsupported','snapshot']) test(`shared inspector preview ${mode}`,async({page})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 await page.setViewportSize({width:1200,height:900});
 await page.route('**/fixture-image.svg',r=>r.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><text x="5" y="50">Test artifact</text></svg>'}));
 await page.route('**/pack-assets/**',r=>r.fulfill({contentType:r.request().url().endsWith('manifest.yaml')?'text/plain':'text/html',body:r.request().url().endsWith('manifest.yaml')?'hide_default_output_previews: true':'<p>Custom viewer fixture</p>'}));
 await page.route('**/api/**',r=>r.fulfill({json:r.request().url().includes('/handles/')?{handle_id:'snapshot-image',node_id:'compute',proxy_url:'/fixture-image.svg?snapshot',storage:'file',absolute_path:'/historical/output.svg',deleted_ts:null,tags:['image'],dim_labels:[],preview:null}:{}}));
 await page.goto(`/e2e/fixtures/inspector-preview.html?mode=${mode}`);
 const inspector=page.locator('.hl-inspector-preview');
 const canvas=page.locator('[data-hololab-node="algorithm"]');
 await expect(inspector).toBeVisible();
 if(['default','custom','snapshot'].includes(mode)){
  await expect(inspector.locator('img')).toBeVisible();
  await expect(canvas.locator('img')).toBeVisible();
  expect(await inspector.locator('img').getAttribute('src')).toBe(await canvas.locator('img').getAttribute('src'));
 }
 if(['custom','hidden','legacy'].includes(mode)){
  await expect(inspector.locator('iframe')).toBeVisible();
  expect(await inspector.locator('iframe').getAttribute('src')).toBe(await canvas.locator('iframe').getAttribute('src'));
 }
 if(['hidden','legacy'].includes(mode)){await expect(inspector.locator('img')).toHaveCount(0);await expect(canvas.locator('img')).toHaveCount(0);}
 if(mode==='empty'){await expect(inspector.locator('img,iframe')).toHaveCount(0);await expect(inspector).toContainText('Run this node to preview its output.');}
 if(mode==='unsupported')await expect(inspector).toContainText('No visualization is available');
 await inspector.scrollIntoViewIfNeeded();
 mkdirSync('/tmp/inspector-preview',{recursive:true});
 for(const theme of ['light','dark']){
  await page.evaluate(t=>document.documentElement.dataset.theme=t,theme);
  await page.screenshot({path:`/tmp/inspector-preview/${mode}-${theme}.png`});
 }
 await page.setViewportSize({width:600,height:900});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 expect(errors).toEqual([]);
});
