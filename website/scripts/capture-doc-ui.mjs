// Read-only documentation capture. Raw inspection material stays in ignored test-results.
import { chromium } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
process.env.PW_TEST_SCREENSHOT_NO_FONTS_READY = '1';
const origin = 'http://117.50.186.141:8828';
const prefix = '3711480d';
const out = 'test-results/doc-ui';
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ args: ['--no-proxy-server'] });
const audit = { workflow: prefix, blockedWrites: [], themes: [] };
async function sanitize(page, name) {
  await page.addStyleTag({ content: `
    [data-hl-node-title] > span:nth-child(2), [data-hl-node-footer] > div:first-child > span:first-child { visibility:hidden!important; }
    .react-flow__node:not([data-id="tri"]) .hololab-node > .nodrag { visibility:hidden!important; }
    img, video, .react-flow__minimap { visibility:hidden!important; }
    .hl-topbar input { visibility:hidden!important; }
    [role="tooltip"] { display:none!important; }
  ` });
  await page.evaluate(name => {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const nodes=[]; while(walker.nextNode()) nodes.push(walker.currentNode);
    for(const n of nodes) {
      if(n.parentElement.closest('script,style')) continue;
      if(n.textContent.includes(name) || /\b[a-z]+\d{4}\b|\b[0-9a-f]{8}(?:-[0-9a-f-]+)?\b|\d{4}-\d{2}-\d{2}|frame_\d+|\d+\s*(?:天前|小时前|分钟前)|\bupdated\b|\b\d+[dhms]\s+ago\b|\/(?:cloud|home|mnt|root|tmp|data|Users|opt)\//i.test(n.textContent)) n.textContent='—';
    }
    for(const e of document.querySelectorAll('[title],[data-tooltip],[aria-description]')) {
      // Prevent native tooltips from exposing metadata during capture.
      e.removeAttribute('title');e.removeAttribute('data-tooltip');e.removeAttribute('aria-description');
    }
  }, name);
  // The one retained viewer is a point cloud. Hide its UI, keeping only the 3D canvas.
  for(const f of page.frames().slice(1)) await f.evaluate(() => {
    const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);while(walker.nextNode())walker.currentNode.textContent='';
    document.querySelectorAll('img,video,input,button,select').forEach(e=>e.style.visibility='hidden');
  }).catch(()=>{});
}
async function shot(page, locator, name, theme) {
  await page.mouse.move(0,0);
  const box=await locator.boundingBox();if(!box)throw Error('Missing '+name);
  await page.screenshot({path:`${out}/${name}-${theme}.png`,clip:{x:Math.max(0,box.x),y:Math.max(0,box.y),width:Math.min(box.width,1600-Math.max(0,box.x)),height:Math.min(box.height,1050-Math.max(0,box.y))},timeout:10000});console.log('captured',name,theme);
}
try {
for(const theme of ['light','dark']) {
  let approvedId = null;
  const c=await browser.newContext({viewport:{width:1600,height:1050},deviceScaleFactor:2,serviceWorkers:'block',colorScheme:theme});
  await c.route('**/*', route => {
    if(['image','media'].includes(route.request().resourceType())) return route.abort();
    if(!['GET','HEAD','OPTIONS'].includes(route.request().method())) {audit.blockedWrites.push({method:route.request().method(),path:new URL(route.request().url()).pathname.replace(/[0-9a-f]{8}-[0-9a-f-]+/g,':id'),theme});return route.abort();}
    if(approvedId && new URL(route.request().url()).pathname === '/api/artifacts') {
      const url=new URL(route.request().url());url.searchParams.set('workflow_id',approvedId);return route.continue({url:url.href});
    }
    return route.continue();
  });
  await c.addInitScript(theme=>localStorage.setItem('hololab.theme',theme),theme);
  const p=await c.newPage();p.setDefaultTimeout(10000);console.log('start',theme);
  await p.goto(origin,{waitUntil:'domcontentloaded',timeout:20000});
  await p.locator('.hl-workflow-card').first().waitFor();
  const response=await p.request.get(origin+'/api/workflows',{timeout:10000});
  const rows=await response.json();const target=rows.find(r=>r.workflow_id.startsWith(prefix));if(!target)throw Error('Approved workflow not found');approvedId=target.workflow_id;
  if(process.env.CAPTURE_ARTIFACTS_ONLY !== '1') {
  await p.evaluate(name=>{
    for(const card of document.querySelectorAll('.hl-workflow-card'))if(!card.textContent.includes(name))card.remove();
    const card=document.querySelector('.hl-workflow-card');if(!card)throw Error('STG card absent');
    // Keep a single approved entry in the existing gallery layout.
    document.querySelector('.hl-gallery-grid').style.gridTemplateColumns='360px';
  },target.name);
  await sanitize(p,target.name);
  await p.evaluate(()=>{document.querySelector('.hl-topbar').style.width='900px';});
  await p.screenshot({path:`${out}/gallery-${theme}.png`,clip:{x:0,y:0,width:900,height:300},animations:'disabled'});
  await p.goto(origin+'/w/'+target.workflow_id,{waitUntil:'domcontentloaded',timeout:20000});
  await p.locator('.react-flow__node[data-id="tri"] iframe').waitFor({timeout:20000});
  await p.waitForTimeout(5000);
  await p.getByPlaceholder('Search packs…').fill('frame-extraction');
  await p.waitForTimeout(500);
  await sanitize(p,target.name);
  await p.evaluate(()=>{
    document.querySelector('.hl-workspace').style.gridTemplateColumns='300px 6px minmax(0, 1fr) 6px 300px';
    for(const e of document.querySelectorAll('.hl-pack-leaf'))if(!e.textContent.trim().startsWith('frame-extraction')) e.remove();
    for(const e of document.querySelectorAll('.hl-pack-folder'))if(e.textContent.trim()==='rig')e.style.display='none';
    const v=document.querySelector('.react-flow__viewport'),box=document.querySelector('.react-flow');
    const nodes=[...document.querySelectorAll('.react-flow__node')].map(e=>{const m=new DOMMatrix(getComputedStyle(e).transform);return {x:m.m41,y:m.m42,w:e.offsetWidth,h:e.offsetHeight};});
    const x=Math.min(...nodes.map(n=>n.x)),y=Math.min(...nodes.map(n=>n.y)),w=Math.max(...nodes.map(n=>n.x+n.w))-x,h=Math.max(...nodes.map(n=>n.y+n.h))-y;
    const scale=Math.min((box.clientWidth-40)/w,(box.clientHeight-40)/h);
    v.style.transform=`translate(${20-x*scale}px,${(box.clientHeight-h*scale)/2-y*scale}px) scale(${scale})`;
  });
  await shot(p,p.locator('.hl-workspace'),'canvas',theme);
  const panels=p.locator('aside.hl-panel');
  await shot(p,panels.first(),'packs-part',theme);
  await shot(p,panels.nth(1).locator(':scope > div').first(),'devices-part',theme);
  if(process.env.CAPTURE_PANELS_ONLY === '1') { await c.close();continue; }
  // Capture history and the current workflow's task panel as actually shown (possibly empty).
  await shot(p,panels.nth(1).locator(':scope > div').last(),'runs-part',theme);
  await shot(p,p.locator('.hl-inspector > div').last(),'jobs-part',theme);
  const toolbar=p.locator('.hl-topbar');
  const run=p.locator('.hl-topbar button').filter({hasText:/^Run$/});
  const rb=await run.boundingBox(),tb=await toolbar.boundingBox();
  await p.screenshot({path:`${out}/run-${theme}.png`,clip:{x:Math.max(tb.x,rb.x-300),y:tb.y,width:Math.min(440,1600-Math.max(tb.x,rb.x-300)),height:tb.height},animations:'disabled'});
  // Only change the browser's visual viewport transform: no graph interaction or persistence.
  async function focus(ids,scale) {
    await p.evaluate(({ids,scale})=>{
      const viewport=document.querySelector('.react-flow__viewport'),container=document.querySelector('.react-flow');
      const positions=ids.map(id=>{const e=document.querySelector(`.react-flow__node[data-id="${id}"]`);const m=new DOMMatrix(getComputedStyle(e).transform);return {x:m.m41,y:m.m42,w:e.offsetWidth,h:e.offsetHeight};});
      const x=Math.min(...positions.map(p=>p.x)),y=Math.min(...positions.map(p=>p.y));
      viewport.style.transform=`translate(${30-x*scale}px,${30-y*scale}px) scale(${scale})`;
    },{ids,scale});
    await p.waitForTimeout(300);
  }
  await focus(['fx'],1);
  const nb=await p.locator('.react-flow__node[data-id="fx"]').boundingBox();await p.screenshot({path:`${out}/node-part-${theme}.png`,clip:{x:nb.x,y:nb.y,width:nb.width,height:190}});
  await focus(['src','fx'],1);
  const a=await p.locator('.react-flow__node[data-id="src"]').boundingBox(),z=await p.locator('.react-flow__node[data-id="fx"]').boundingBox();
  await p.screenshot({path:`${out}/ports-${theme}.png`,clip:{x:a.x-8,y:Math.min(a.y,z.y)-8,width:z.x+z.width-a.x+16,height:190},animations:'disabled'});
  await focus(['tri'],1.2);
  await shot(p,p.locator('.react-flow__node[data-id="tri"]'),'preview',theme);
  // Inspect actual localized labels without exporting workflow metadata.
  audit.themes.push({theme, chineseLabels:await p.locator('body').evaluate(e=>[...new Set(e.innerText.match(/[\u4e00-\u9fff]+/g)||[])].filter(s=>s.length>1))});
  }
  await p.goto(origin+'/artifacts',{waitUntil:'domcontentloaded',timeout:20000});
  await p.locator('select').first().waitFor();
  const select=p.locator('select').filter({has:p.locator(`option[value="${target.workflow_id}"]`)});
  await select.selectOption(target.workflow_id);
  await p.waitForTimeout(1000);
  await sanitize(p,target.name);
  await p.evaluate(()=>{
    // No other workflow labels, even inside dropdowns, survive into the capture.
    document.querySelectorAll('select').forEach(s=>{for(const o of [...s.options])if(!o.selected)o.remove();});
    document.querySelectorAll('code').forEach(e=>e.textContent='—');
  });
  await p.screenshot({path:`${out}/artifacts-${theme}.png`,clip:{x:0,y:0,width:1600,height:570},animations:'disabled'});
  await c.close();
}
await writeFile(`${out}/${process.env.CAPTURE_PANELS_ONLY === '1' ? 'audit-panels' : process.env.CAPTURE_ARTIFACTS_ONLY === '1' ? 'audit-artifacts' : 'audit'}.json`,JSON.stringify(audit,null,2));
} finally { await browser.close(); }
console.log('Captured approved STG views in both themes; write requests blocked:',audit.blockedWrites.length);
