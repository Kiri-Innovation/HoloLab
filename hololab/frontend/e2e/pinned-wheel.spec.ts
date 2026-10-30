import {test,expect} from '@playwright/test';
import fs from 'node:fs';
test.use({video:'on',viewport:{width:1400,height:950}});
test('pinned fields pass wheel to canvas without changing focused numeric values',async({page})=>{
 const spec=(type:string,value:unknown)=>({type,default:value,pinned:true,optional:false});
 const pack={name:'wheel-test',version:'1',manifest_hash:'test',node_ids:['device'],inputs:{},outputs:{},params:{count:spec('int',17),mode:{...spec('enum','a'),values:['a','b']},path:spec('path','/')}};
 let graph:any={nodes:[{id:'test',algorithm_name:pack.name,algorithm_version:'1',assigned_node_id:'device',params:{count:17,mode:'a',path:'/'},position:{x:0,y:0}}],edges:[]};let version=1;
 await page.routeWebSocket('**',()=>{});
 await page.route('**/api/**',async route=>{
  const req=route.request(),path=new URL(req.url()).pathname;let body:any=[];
  if(path==='/api/pack-catalog')body=[pack];
  else if(path==='/api/nodes/metrics/history')body={nodes:{},sample_interval_s:3};
  else if(path==='/api/workflows/wheel')body={workflow_id:'wheel',name:'Pinned wheel verification',graph,created_ts:1,updated_ts:version};
  else if(path==='/api/workflows'&&req.method()==='POST'){const draft=req.postDataJSON();expect(draft.base_updated_ts).toBe(version);graph=draft.graph;body={workflow_id:'wheel',updated_ts:++version};}
  else if(path.includes('/paths'))body={path:'/',entries:[],has_more:false};
  await route.fulfill({json:body});
 });
 await page.goto('/w/wheel');const pinned=page.getByRole('group',{name:'Pinned parameters',exact:true});await expect(pinned).toBeVisible();
 const viewport=page.locator('.react-flow__viewport');const transform=()=>viewport.evaluate(el=>getComputedStyle(el).transform);
 fs.mkdirSync('/tmp/pinned-wheel',{recursive:true});
 // CDP records actual native wheel listeners on the container/ancestor path.
 const cdp=await page.context().newCDPSession(page);
 const remote=await cdp.send('Runtime.evaluate',{expression:'document.querySelector(".hl-pinned-parameters")'});
 const listeners=await cdp.send('DOMDebugger.getEventListeners',{objectId:remote.result.objectId!});
 fs.writeFileSync('/tmp/pinned-wheel/listeners.json',JSON.stringify(listeners,null,2));
 fs.writeFileSync('/tmp/pinned-wheel/element.json',JSON.stringify(await pinned.evaluate(el=>({classes:el.className,overflow:getComputedStyle(el).overflow,ancestors:Array.from((function*(n:Element|null){while(n){yield n;n=n.parentElement;}})(el)).map(n=>n.className)})),null,2));
 for(const theme of ['light','dark']){
  await page.evaluate(t=>document.documentElement.dataset.theme=t,theme);
  const input=pinned.getByRole('spinbutton',{name:'count'});await input.fill('23');await expect.poll(()=>graph.nodes[0].params.count).toBe(23);
  await input.hover();const before=await transform();await page.mouse.wheel(0,80);
  if(process.env.DIAGNOSE_WHEEL){await page.waitForTimeout(400);fs.writeFileSync('/tmp/pinned-wheel/before.json',JSON.stringify({before,after:await transform(),value:await input.inputValue()}));return;}
  await expect.poll(transform).not.toBe(before);fs.writeFileSync(`/tmp/pinned-wheel/${theme}-after.json`,JSON.stringify({before,after:await transform(),value:await input.inputValue()}));await expect(input).toHaveValue('23');await expect(input).toBeFocused();
  await pinned.locator('.hl-pinned-label').first().hover();const overLabel=await transform();await page.mouse.wheel(0,-80);await expect.poll(transform).not.toBe(overLabel);
  await page.screenshot({path:`/tmp/pinned-wheel/${theme}.png`});
 }
 await pinned.getByRole('combobox',{name:'mode'}).click();await page.getByRole('option',{name:'b',exact:true}).click();await expect.poll(()=>graph.nodes[0].params.mode).toBe('b');
 await pinned.getByRole('button',{name:'Browse paths for path'}).click();await expect(page.getByRole('dialog')).toBeVisible();await page.getByRole('button',{name:'Close path browser'}).click();
 await pinned.getByRole('combobox',{name:'path',exact:true}).fill('/data');await expect.poll(()=>graph.nodes[0].params.path).toBe('/data');
 await page.getByRole('tab',{name:'Runs',exact:true}).click();await page.getByRole('tab',{name:'Inspector',exact:true}).click();await expect(pinned.getByRole('spinbutton',{name:'count'})).toHaveValue('23');
 await page.reload();await expect(pinned.getByRole('spinbutton',{name:'count'})).toHaveValue('23');
});
