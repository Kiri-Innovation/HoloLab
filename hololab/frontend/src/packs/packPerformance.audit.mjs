// Run against a temporary gateway containing a copy of real draft data.
import {chromium, expect} from '@playwright/test';
import {mkdir,writeFile} from 'node:fs/promises';
const base=process.env.PACK_PERF_URL || 'http://127.0.0.1:8897';
const output=process.env.PACK_PERF_OUTPUT || '/tmp/pack-performance';
await mkdir(output,{recursive:true});
const key=process.env.PACK_PERF_KEY || 'builtin/frame-extraction@0.1.0';
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1050}});
const requests=[],errors=[],samples=[];
page.on('request',r=>{if(new URL(r.url()).pathname.startsWith('/api/'))requests.push(new URL(r.url()).pathname);});
page.on('pageerror',e=>errors.push(e.message));
const detail=page.locator(`[data-pack-key="${key}"]`);
try {
 const start=performance.now();
 await page.goto(`${base}/packs?view=local&section=category&pack=${encodeURIComponent(key)}`);
 await expect(detail.getByRole('heading',{level:1})).toBeVisible();
 const directMs=performance.now()-start;
 expect(directMs).toBeLessThan(500);
 await expect(detail.getByText(/12 条工作流/, {exact:false})).toBeVisible();
 for(let i=0;i<10;i++){
  await page.getByRole('navigation',{name:'本地视图'}).getByRole('button',{name:'总览',exact:true}).click();
  const button=page.locator('.hl-local-results .hl-pack-project-link').filter({hasText:/^frame-extraction$/});
  await expect(button).toBeVisible();
  const before=requests.length,clicked=performance.now();
  await button.click();await expect(detail.getByRole('heading',{level:1})).toBeVisible();
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const ms=performance.now()-clicked;samples.push(ms);
  await expect(detail.getByText(/12 条工作流/, {exact:false})).toBeVisible();
  expect(ms).toBeLessThan(500);
  const selectionRequests=requests.slice(before).filter(path=>path.endsWith('/usage'));
  expect(selectionRequests).toEqual([`/api/packs/${encodeURIComponent(key)}/usage`]);
 }
 await page.screenshot({path:`${output}/detail.png`,fullPage:true,animations:'disabled'});
 await page.getByRole('navigation',{name:'本地视图'}).getByRole('button',{name:'总览',exact:true}).click();
 await page.route(`**/api/packs/${encodeURIComponent(key)}/usage`,async route=>{await new Promise(r=>setTimeout(r,1500));await route.continue();});
 const clicked=performance.now();await page.locator('.hl-local-results .hl-pack-project-link').filter({hasText:/^frame-extraction$/}).click();
 await expect(detail.getByRole('heading',{level:1})).toBeVisible();const slowUsagePageMs=performance.now()-clicked;
 expect(slowUsagePageMs).toBeLessThan(500);await expect(detail).toContainText('引用加载中');
 await expect(detail.getByText(/12 条工作流/, {exact:false})).toBeVisible();
 expect(requests.filter(path=>path.startsWith('/api/workflows')||path==='/api/recipes/check')).toEqual([]);
 expect(errors).toEqual([]);
 const result={direct_navigation_ms:directMs,click_samples_ms:samples,click_max_ms:Math.max(...samples),delayed_usage_page_ms:slowUsagePageMs,requests};
 await writeFile(`${output}/browser.json`,JSON.stringify(result,null,2));console.log(JSON.stringify({...result,requests:`${requests.length} API requests; no workflow graph/list/check fan-out`},null,2));
}finally{await browser.close();}
