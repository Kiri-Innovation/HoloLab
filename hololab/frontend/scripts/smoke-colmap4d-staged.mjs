// Uses the actual component via Vite with the production gateway /proxy route.
// Acceptance must keep this transport; do not replace it with a direct node proxy.
import { chromium } from '@playwright/test';
import fs from 'node:fs';
const origin = process.argv[2] || 'http://127.0.0.1:15174';
const out = process.argv[3] || '../../reports/colmap4d-staged';
fs.mkdirSync(out, {recursive:true});
const node = '3038e635-4840-46dd-aa0c-6bd9c477b852';
const work = 'w/9967b1f8-3646-41f5-a269-394b6d392169/j/';
const model = work + '5eba9322-2842-46ff-8e61-b0c8f63cb760/colmap4d';
const cams = work + '03fb2084-8f8c-43c2-9bda-aa27955ede38/4d_cams';
const browser = await chromium.launch({headless:true,args:['--no-sandbox','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
const imageGate = process.argv.includes('--image-gate');
const evidence = {origin, imageGate, errors:[], requests:[]};
try {
 const page = await browser.newPage({viewport:{width:1200,height:850}});
 await page.addInitScript(() => {
   window.__geometry = {lines: 0, points: 0};
   for (const type of [window.WebGLRenderingContext, window.WebGL2RenderingContext]) {
     if (!type) continue;
     for (const key of ['drawArrays', 'drawElements', 'drawArraysInstanced', 'drawElementsInstanced']) {
       const original = type.prototype[key]; if (!original) continue;
       type.prototype[key] = function(...args) {
         const count = args[key.startsWith('drawArrays') ? 2 : 1];
         if (args[0] === 1 && count > 0) window.__geometry.lines = Math.max(window.__geometry.lines, count);
         if (key.endsWith('Instanced')) window.__geometry.points = Math.max(window.__geometry.points, args[key.startsWith('drawArrays') ? 3 : 4]);
         return original.apply(this, args);
       };
     }
   }
 });
 page.on('response', async r=>{if(r.status()>=400)console.log('HTTP',r.status(),r.url(),await r.text().catch(()=>''));});
 page.on('pageerror', e => {evidence.errors.push(e.message); console.log('PAGE ERROR',e.message);});
 page.on('request', r => {if(r.url().includes('/proxy/')) evidence.requests.push({url:r.url(),range:r.headers().range});});
 if (imageGate) await page.route('**/_format/colmap4d/**?part=metadata', async route => {
   const response = await route.fetch(); const data = await response.json();
   data.result.images_bytes += 201000000; // Explicit metadata fixture; pixels stay real.
   return route.fulfill({response, json: data});
 });
 await page.route('**/__staged*', route => {
  const only = new URL(route.request().url()).searchParams.has('cams');
  const dir = only ? cams : model;
  return route.fulfill({contentType:'text/html',body:`<html><head><meta charset="utf-8"></head><body style="margin:20px;background:#111827;color:white"><h2>${only ? 'colmap4d-cams — real split output' : 'colmap4d — real 4 GB model'}</h2><div id="root"></div><script type="module">
  import RefreshRuntime from '/@react-refresh'; RefreshRuntime.injectIntoGlobalHook(window); window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>(type)=>type;window.__vite_plugin_react_preamble_installed__=true;
  const {default: React} = await import('/node_modules/.vite/deps/react.js');const {default: {createRoot}} = await import('/node_modules/.vite/deps/react-dom_client.js');const {Colmap4DPreview}=await import('/src/canvas/Colmap4DPreview.tsx');
  createRoot(document.getElementById('root')).render(React.createElement(Colmap4DPreview,${JSON.stringify({baseUrl:`/proxy/${node}/${dir}${only?'':'/sparse/0'}`,imagesUrl:`/proxy/${node}/${dir}/images`,camsOnly:only,nodeId:node,absolutePath:`/cloud/cloud-ssd1/Kiri4DGS/output/hololab/${dir}`})}));</script></body></html>`});
 });
 await page.goto(origin+'/__staged?cams');
 await page.waitForFunction(() => {
   const status = document.querySelector('[role="status"]');
   return status?.textContent.includes('已加载') || status?.querySelector('button');
 }, undefined, {timeout: 30000});
 if (!(await page.getByRole('status').innerText()).includes('已加载')) {
   evidence.transportFailure = await page.getByRole('status').innerText();
   await page.screenshot({path:out+'/transport-failure.png'});
   throw new Error(evidence.transportFailure);
 }
 await page.waitForTimeout(2500);
 const cameraFrame = page.frames().find(f=>f.url().includes('/colmaputil/'));
 await cameraFrame.waitForFunction(() => window.__geometry.lines > 0);
 evidence.cameraDraws = await cameraFrame.evaluate(() => window.__geometry);
 await page.screenshot({path:out+'/cams.png'});
 evidence.camsText = await page.locator('body').innerText();
 evidence.camsFrame = await page.frames().find(f=>f.url().includes('/colmaputil/')).locator('body').innerText();
 await page.goto(origin+'/__staged');
 const points = page.locator('[data-resource="points"]');
 await points.getByRole('button',{name:'跳过',exact:true}).waitFor({timeout:180000});
 const images = page.locator('[data-resource="images"]');
 if (imageGate) {
   await images.getByRole('button',{name:'跳过',exact:true}).waitFor({timeout:30000});
   evidence.imagesBeforeConsent = evidence.requests.filter(r=>/\.(png|jpg|jpeg)$/i.test(r.url));
   if (evidence.imagesBeforeConsent.length) throw Error('Images fetched before consent');
   await page.screenshot({path:out+'/both-ask-simulated-size.png'});
   await images.getByRole('button',{name:'跳过',exact:true}).click();
   await points.getByRole('button',{name:'跳过',exact:true}).click();
   await page.screenshot({path:out+'/both-skipped-simulated-size.png'});
   await images.getByRole('button',{name:'加载',exact:true}).click();
 }
 await images.filter({hasText:'已加载'}).waitFor({timeout:180000});
 await page.waitForTimeout(2500);
 await page.screenshot({path:out+'/ask.png'});
 evidence.beforeConsent = evidence.requests.filter(r=>r.url.includes('points'));
 if(evidence.beforeConsent.some(r=>!r.range)) throw Error('Point body fetched before consent');
 if (!imageGate) await points.getByRole('button',{name:'跳过',exact:true}).click();
 await page.screenshot({path:out+'/skip.png'});
 if (!imageGate) {
 await points.getByRole('button',{name:'加载',exact:true}).click();
 await points.filter({hasText:'已加载'}).waitFor({timeout:240000});
 await page.waitForTimeout(4000);
 const pointFrame = page.frames().find(f=>f.url().includes('/colmaputil/'));
 await pointFrame.waitForFunction(() => window.__geometry.points > 0);
 evidence.pointDraws = await pointFrame.evaluate(() => window.__geometry);
 await page.screenshot({path:out+'/loaded.png'});
 }
 evidence.finalText = await page.locator('body').innerText();
 evidence.finalFrame = await page.frames().find(f=>f.url().includes('/colmaputil/')).locator('body').innerText();
 console.log(JSON.stringify({errors:evidence.errors,finalText:evidence.finalText,camsFrame:evidence.camsFrame.slice(0,500),finalFrame:evidence.finalFrame.slice(0,500)},null,2));
} finally {fs.writeFileSync(out+'/evidence.json',JSON.stringify(evidence,null,2));await browser.close();}
