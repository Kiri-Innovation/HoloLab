// Read-only STG documentation capture; raw material stays in ignored test-results.
import { chromium } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
process.env.PW_TEST_SCREENSHOT_NO_FONTS_READY='1';
const origin='http://117.50.186.141:8828',workflow='3711480d-e858-453c-bf5b-76af602e9805',out='test-results/agent-oriented';
await mkdir(out,{recursive:true});
const browser=await chromium.launch({args:['--no-proxy-server']});
const audit={workflow,blockedWrites:[],themes:[],bounds:{}};
async function sanitize(page,name){
 await page.addStyleTag({content:`.hl-topbar input,.react-flow__minimap,img,video{visibility:hidden!important} [data-hl-node-title]>span:nth-child(2),[data-hl-node-footer]>div:first-child>span:first-child{visibility:hidden!important}.react-flow__node:not([data-id="tri"]) iframe{visibility:hidden!important}`});
 await page.evaluate(name=>{
  window.__docScrub?.disconnect();
  const sensitive=/\b[0-9a-f]{8}(?:-[0-9a-f-]+)?\b|\d{4}-\d{2}-\d{2}|\b\d{1,2}:\d{2}:\d{2}\b|frame[_-]?\d+|\b\d+(?:\.\d+)?\s*(?:ms|s|m|h|d)\b|\d+\s*(?:天前|小时前|分钟前)|\/(?:cloud|home|mnt|root|tmp|data|Users|opt)\//i;
  const scrub=()=>{
   const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT),nodes=[];while(walker.nextNode())nodes.push(walker.currentNode);
   for(const n of nodes){if(n.parentElement.closest('script,style'))continue;if(n.textContent.includes(name)||/kiri4090/i.test(n.textContent)||sensitive.test(n.textContent))n.textContent='—';}
   for(const el of document.querySelectorAll('[title],[data-tooltip],[aria-description]')){el.removeAttribute('title');el.removeAttribute('data-tooltip');el.removeAttribute('aria-description');}
  };
  const observer=new MutationObserver(()=>{observer.disconnect();scrub();observer.observe(document.body,{subtree:true,childList:true,characterData:true});});
  scrub();observer.observe(document.body,{subtree:true,childList:true,characterData:true});window.__docScrub=observer;
 },name);
 // Keep only the previously public point-cloud canvas; no source imagery or viewer metadata.
 for(const f of page.frames().slice(1))await f.evaluate(()=>{
  const w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);while(w.nextNode())if(!w.currentNode.parentElement.closest('script,style'))w.currentNode.textContent='';
  document.querySelectorAll('img,video,input,button,select').forEach(e=>e.style.visibility='hidden');
 }).catch(()=>{});
}
// Reframe only this browser viewport; do not change graph positions or save layout.
async function focus(p,ids,maxScale=1){
 await p.evaluate(({ids,maxScale})=>{
  const v=document.querySelector('.react-flow__viewport'),c=document.querySelector('.react-flow');
  const ps=ids.map(id=>{const e=document.querySelector(`.react-flow__node[data-id="${id}"]`),m=new DOMMatrix(getComputedStyle(e).transform);return{x:m.m41,y:m.m42,w:e.offsetWidth,h:e.offsetHeight};});
  const x=Math.min(...ps.map(p=>p.x)),y=Math.min(...ps.map(p=>p.y)),w=Math.max(...ps.map(p=>p.x+p.w))-x,h=Math.max(...ps.map(p=>p.y+p.h))-y,s=Math.min(maxScale,(c.clientWidth-60)/w,(c.clientHeight-60)/h);
  v.style.transform=`translate(${(c.clientWidth-w*s)/2-x*s}px,${(c.clientHeight-h*s)/2-y*s}px) scale(${s})`;
 },{ids,maxScale});await p.waitForTimeout(250);
}
try { for (const theme of ['light','dark']) {
 const c=await browser.newContext({viewport:{width:1600,height:1050},deviceScaleFactor:2,serviceWorkers:'block',colorScheme:theme});
 await c.route('**/*', async route => {
  const q=route.request();
  if(!['GET','HEAD','OPTIONS'].includes(q.method())){audit.blockedWrites.push({method:q.method(),url:new URL(q.url()).pathname});return route.abort();}
  if(['image','media'].includes(q.resourceType()))return route.abort();
  return route.continue();
 });
 await c.addInitScript(t=>localStorage.setItem('hololab.theme',t),theme);
 const p=await c.newPage();
 const target=await(await p.request.get(`${origin}/api/workflows/${workflow}`)).json();
 await p.goto(`${origin}/w/${workflow}`,{waitUntil:'domcontentloaded'});
 const node=p.locator('.react-flow__node[data-id="fx"]');await node.waitFor({timeout:30000});await p.waitForTimeout(3000);
 await focus(p,['fx']);await sanitize(p,target.name);
 const reference=node.getByRole('button',{name:/复制引用给 AI/}).first();
 await reference.hover();await p.waitForTimeout(800);
 const box=await node.boundingBox();
 await p.screenshot({path:`${out}/reference-entry-${theme}.png`,clip:{x:box.x-10,y:box.y-35,width:box.width+20,height:140},animations:'disabled'});
 audit.themes.push({theme,referenceLabel:await reference.getAttribute('aria-label')});
 await c.close();
} await writeFile(`${out}/audit.json`,JSON.stringify(audit,null,2)); }
finally {await browser.close();}
