// Offline visual comparison of real library-produced subsets. No live data
// transport claims, source parser, backend change, or production restart.
import {chromium} from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
const origin = process.argv[2] || 'http://127.0.0.1:8828';
const data = process.argv[3] || '/tmp/colmap4d-jitter-20261106';
const out = process.argv[4] || '../../reports/colmap4d-jitter-20261106';
fs.mkdirSync(out,{recursive:true});
const browser = await chromium.launch({headless:true,args:['--no-sandbox','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
const evidence={mode:'Offline sampling visualization; not a live transport acceptance test',errors:[],networkFailures:[],draws:{}};
try {
 const page=await browser.newPage({viewport:{width:2000,height:820},deviceScaleFactor:1});
 await page.addInitScript(()=>{
   localStorage.setItem('colmap-util-pointcloud',JSON.stringify({pointSize:3,renderMode:'cpu'}));
   localStorage.setItem('colmap-util-camera',JSON.stringify({cameraMode:'fps',showFrustumWireframes:false,showImagePlane:false,cameraFov:61.5}));
   localStorage.setItem('colmap-util-ui',JSON.stringify({axesDisplayMode:'none',backgroundColor:'#171b21'}));
   localStorage.setItem('colmap-util-time',JSON.stringify({posFrac:0,sigmaFrac:0,softFrac:0}));
   localStorage.setItem('colmaputil.default_view',JSON.stringify({v:2,mode:'fps',pos:[0,0,0],yaw:Math.PI,pitch:0,fov:61.5}));
   window.__draws=[];
   const original=WebGL2RenderingContext.prototype.drawArraysInstanced;
   WebGL2RenderingContext.prototype.drawArraysInstanced=function(...args){if(args[3]>0)window.__draws.push(args[3]);return original.apply(this,args);};
 });
 page.on('pageerror',e=>evidence.errors.push(e.message));
 page.on('response',r=>{if(r.status()>=400)evidence.networkFailures.push({url:r.url(),status:r.status()});});
 await page.route('**/__colmap_sampling_compare',route=>route.fulfill({contentType:'text/html',body:`<!doctype html><html><meta charset="utf-8"><body style="margin:20px;background:#101419;color:#eee;font-family:sans-serif"><h2>真实 41,990,400 点 · 抽样前后对比</h2><p>同一份源数据、同一视角、t=0、点大小 3。预算均为 200,000；无补点或图像纹理。</p><div style="display:flex;gap:20px">${[['old_frontend','旧：固定步长 210（199,955 点）'],['jitter','新：分层抖动 seed=0（200,000 点）']].map(([id,title])=>`<section><h3>${title}</h3><iframe id="${id}" src="/colmaputil/index.html?embed=1" style="width:960px;height:620px;border:1px solid #55606b"></iframe></section>`).join('')}</div><script>window.ready=new Set();addEventListener('message',e=>{const f=[...document.querySelectorAll('iframe')].find(f=>f.contentWindow===e.source);if(f&&e.data?.type==='colmap-ready')ready.add(f.id);});</script></body></html>`}));
 await page.goto(origin+'/__colmap_sampling_compare');
 await page.waitForFunction(()=>window.ready.size===2);
 for(const key of ['old_frontend','jitter']){
   const files=new Map();
   for(const folder of ['cameras',key])for(const name of fs.readdirSync(path.join(data,folder)))files.set(name,{name,base64:fs.readFileSync(path.join(data,folder,name)).toString('base64')});
   await page.evaluate(({key,files})=>{
     const entries=files.map(f=>({name:f.name,blob:new Blob([Uint8Array.from(atob(f.base64),c=>c.charCodeAt(0))])}));
     document.getElementById(key).contentWindow.postMessage({type:'colmap-load-files',files:entries,name:key},location.origin);
   },{key,files:[...files.values()]});
 }
 for(const key of ['old_frontend','jitter']){
   const element=await page.locator('#'+key).elementHandle();
   const target=await element.contentFrame();
   await target.waitForFunction(()=>window.__draws.length>0);
   evidence.draws[key]=await target.evaluate(()=>[...new Set(window.__draws)]);
   evidence[key]=await target.locator('body').innerText();
 }
 await page.waitForTimeout(1500);
 await page.screenshot({path:path.join(out,'viewer-before-after.png')});
 console.log(JSON.stringify(evidence,null,2));
 if(evidence.errors.length||evidence.networkFailures.length)throw Error('Viewer comparison had errors');
}finally{fs.writeFileSync(path.join(out,'viewer-evidence.json'),JSON.stringify(evidence,null,2));await browser.close();}
