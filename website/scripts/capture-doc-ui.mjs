// Read-only STG documentation capture; raw material stays in ignored test-results.
import { chromium } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
process.env.PW_TEST_SCREENSHOT_NO_FONTS_READY='1';
const origin='http://117.50.186.141:8828',workflow='3711480d-e858-453c-bf5b-76af602e9805',out='test-results/doc-ui';
await mkdir(out,{recursive:true});
const browser=await chromium.launch({args:['--no-proxy-server']});
const audit={workflow,blockedWrites:[],themes:[],bounds:{}};
async function sanitize(page,name){
 await page.addStyleTag({content:`.hl-topbar input,.react-flow__minimap,img,video{visibility:hidden!important} [data-hl-node-title]>span:nth-child(2),[data-hl-node-footer]>div:first-child>span:first-child{visibility:hidden!important}.react-flow__node:not([data-id="tri"]) iframe{visibility:hidden!important}[role="tooltip"]{display:none!important}`});
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
async function shot(p,locator,name,theme,height,width){
 await p.mouse.move(0,0);const b=await locator.boundingBox();if(!b)throw Error('Missing '+name);const v=p.viewportSize();
 await p.screenshot({path:`${out}/${name}-${theme}.png`,clip:{x:Math.max(0,b.x),y:Math.max(0,b.y),width:Math.min(width??b.width,v.width-Math.max(0,b.x)),height:Math.min(height??b.height,v.height-Math.max(0,b.y))},timeout:20000,animations:'disabled'});console.log('captured',name,theme);
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
try{for(const theme of ['light','dark']){
 const c=await browser.newContext({viewport:{width:1600,height:1050},deviceScaleFactor:2,serviceWorkers:'block',colorScheme:theme});
 await c.route('**/*',async r=>{
  const q=r.request(),u=new URL(q.url());
  if(!['GET','HEAD','OPTIONS'].includes(q.method())){audit.blockedWrites.push({method:q.method(),path:u.pathname,theme});return r.abort();}
  if(['image','media'].includes(q.resourceType()))return r.abort();
  if(u.origin===origin&&u.pathname==='/api/workflows'){const response=await r.fetch(),rows=await response.json();return r.fulfill({response,json:rows.filter(x=>x.workflow_id===workflow)});}
  if(u.origin===origin&&u.pathname==='/api/artifacts'){u.searchParams.set('workflow_id',workflow);return r.continue({url:u.href});}
  return r.continue();
 });
 await c.addInitScript(t=>localStorage.setItem('hololab.theme',t),theme);
 const p=await c.newPage();p.setDefaultTimeout(15000);
 await p.goto(origin,{waitUntil:'domcontentloaded'});await p.locator('.hl-workflow-card').waitFor();
 const target=await(await p.request.get(`${origin}/api/workflows/${workflow}`)).json();
 await sanitize(p,target.name);await shot(p,p.locator('.hl-workflow-card'),'gallery',theme);
 await p.goto(`${origin}/w/${workflow}`,{waitUntil:'domcontentloaded'});await p.locator('.react-flow__node[data-id="tri"] iframe').waitFor({timeout:30000});await p.waitForTimeout(5000);
 await p.getByPlaceholder('Search packs…').fill('frame-extraction');await p.waitForTimeout(400);
 const chineseLabels=await p.locator('body').evaluate(e=>[...new Set(e.innerText.match(/[\u4e00-\u9fff]+/g)||[])].filter(s=>s.length>1));
 await sanitize(p,target.name);await focus(p,await p.locator('.react-flow__node').evaluateAll(ns=>ns.map(n=>n.dataset.id)));
 audit.bounds[theme]=await p.evaluate(()=>Object.fromEntries(Object.entries({canvas:'.react-flow',packs:'aside.hl-panel',history:'.hl-inspector-sidebar',jobs:'#hl-bottom-tab-jobs',preview:'.react-flow__node[data-id="tri"] iframe'}).map(([k,s])=>[k,document.querySelector(s)?.getBoundingClientRect().toJSON()])));
 await shot(p,p.locator('.hl-workspace'),'canvas',theme);
 await shot(p,p.locator('aside.hl-panel').first(),'packs-part',theme,290);
 await shot(p,p.locator('.hl-compute-dock'),'devices-part',theme,330,420);
 await p.getByRole('tab',{name:'Jobs',exact:true}).click();await p.waitForTimeout(500);
 await shot(p,p.locator('.hl-compute-dock'),'jobs-part',theme,350);
 await shot(p,p.locator('.hl-inspector-sidebar'),'runs-part',theme,350);
 await p.getByRole('tab',{name:'Compute Nodes · Pulse',exact:true}).click();
 const rb=await p.locator('.hl-topbar button').filter({hasText:/^Run$/}).boundingBox();
 await p.screenshot({path:`${out}/run-${theme}.png`,clip:{x:rb.x-220,y:0,width:1600-rb.x+220,height:48},animations:'disabled'});
 await focus(p,['fx']);await shot(p,p.locator('.react-flow__node[data-id="fx"]'),'node-part',theme,200);
 await focus(p,['src','fx']);const a=await p.locator('.react-flow__node[data-id="src"]').boundingBox(),z=await p.locator('.react-flow__node[data-id="fx"]').boundingBox();
 await p.screenshot({path:`${out}/ports-${theme}.png`,clip:{x:Math.min(a.x,z.x)-6,y:Math.min(a.y,z.y)-6,width:Math.max(a.x+a.width,z.x+z.width)-Math.min(a.x,z.x)+12,height:Math.min(200,Math.max(a.height,z.height))},animations:'disabled'});
 await focus(p,['tri']);await sanitize(p,target.name);await shot(p,p.locator('.react-flow__node[data-id="tri"]'),'preview',theme);
 audit.themes.push({theme,chineseLabels});
 await p.goto(`${origin}/artifacts`,{waitUntil:'domcontentloaded'});
 const select=p.locator('select').filter({has:p.locator(`option[value="${workflow}"]`)});await select.selectOption(workflow);await p.waitForTimeout(1000);await sanitize(p,target.name);
 await p.evaluate(()=>{document.querySelectorAll('select').forEach(s=>{for(const o of [...s.options])if(!o.selected)o.remove();});document.querySelectorAll('code').forEach(e=>e.textContent='—');});
 await p.screenshot({path:`${out}/artifacts-${theme}.png`,clip:{x:0,y:0,width:1600,height:570},animations:'disabled'});await c.close();
}await writeFile(`${out}/audit.json`,JSON.stringify(audit,null,2));}finally{await browser.close();}
console.log('Captured STG views; blocked writes:',audit.blockedWrites.length);
