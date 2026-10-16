import { test, expect } from '@playwright/test';
import { mkdirSync } from 'node:fs';
for (const legacy of [false, true]) test(`enum controls preserve raw values (legacy=${legacy})`, async ({page}) => {
 const pack = {name:'stg-train',version:'0.3.0',manifest_hash:'enum',node_ids:['compute'],inputs:{},outputs:{},params:{
  motion_mask_mode:{type:'enum',default:'depth_p10p90',values:['gmc_residual','disp_p10p90','depth_p10p90']},
  numeric:{type:'enum',default:1,values:[1,2,'2']},
  missing:{type:'enum',default:'original'}
 }};
 let graph:any={nodes:[{id:'stg',algorithm_name:pack.name,algorithm_version:pack.version,position:{x:100,y:100},params:legacy?{motion_mask_mode:'legacy'}:{},assigned_node_id:'compute',arrayed_toggle:false}],edges:[]};
 const saved:any[]=[]; let dispatched:any=null;
 await page.routeWebSocket('**',()=>{});
 await page.route('**/api/**',async route=>{
  const path=new URL(route.request().url()).pathname;let body:any=[];
  if(path==='/api/workflows/enum/run'){dispatched=structuredClone(graph);body={workflow_id:'enum',snapshot_id:'enum-run',node_count:1};}
  else if(path.includes('/snapshots/'))body={snapshot_id:'enum-run',workflow_id:'enum',graph,created_ts:1};
  else if(path==='/api/pack-catalog')body=[pack];
  else if(path==='/api/nodes')body=[{node_id:'compute',node_name:'Compute',packs:[pack],gpu:{count:0},connected_ts:1}];
  else if(path==='/api/nodes/metrics/history')body={sample_interval_s:3,nodes:{}};
  else if(path==='/api/workflows/enum')body={workflow_id:'enum',name:'Enum controls',graph,created_ts:1,updated_ts:1};
  else if(path==='/api/workflows'&&route.request().method()==='POST'){const data=route.request().postDataJSON();saved.push(data);graph=data.graph;body={workflow_id:'enum',updated_ts:1};}
  await route.fulfill({json:body});
 });
 await page.setViewportSize({width:1440,height:1050});
 await page.goto('/w/enum');
 await page.locator('[data-hololab-node="algorithm"]').click();
 const panel=page.locator('.hl-node-inspector');
 const mode=panel.getByLabel('motion_mask_mode',{exact:true});
 await expect(mode).toHaveJSProperty('tagName',legacy?'INPUT':'BUTTON');
 await expect(panel.getByLabel('missing',{exact:true})).toHaveValue('original');
 if(legacy){await expect(mode).toHaveValue('legacy');await expect(panel.getByText('Current value is not in the available options.')).toBeVisible();}
 else {
  await mode.click();
  await expect(page.getByRole('option',{name:'depth_p10p90',exact:true})).toHaveAttribute('aria-selected','true');
  const box = (await mode.boundingBox())!;
  const popup = (await page.getByRole('listbox').boundingBox())!;
  expect(popup.y).toBeGreaterThanOrEqual(box.y+box.height);
  expect(popup.width).toBeCloseTo(box.width,0);
  await expect(mode).toHaveCSS('height','24px');
  mkdirSync('/tmp/enum-custom',{recursive:true});
  for(const theme of ['light','dark']) {
    await page.evaluate(t=>document.documentElement.dataset.theme=t,theme);
    await page.waitForTimeout(250);
    await page.screenshot({path:`/tmp/enum-custom/open-${theme}.png`,clip:{x:1050,y:150,width:390,height:500}});
  }
  await mode.press('Home'); await mode.press('Enter');
  const numeric=panel.getByLabel('numeric',{exact:true});
  await numeric.focus(); await numeric.press('ArrowDown'); await numeric.press('ArrowDown'); await numeric.press('Enter');
  await expect.poll(()=>saved.at(-1)?.graph.nodes[0].params.numeric).toBe(2);
  expect(saved.at(-1).graph.nodes[0].params.motion_mask_mode).toBe('gmc_residual');
  await numeric.click(); await numeric.press('End'); await numeric.press('Enter');
  await expect.poll(()=>saved.at(-1)?.graph.nodes[0].params.numeric).toBe('2');
  await mode.click(); await mode.press('Escape'); await expect(page.getByRole('listbox')).toHaveCount(0);
  await mode.click(); await panel.getByRole('heading',{name:'Parameters',exact:true}).click();
  await expect(page.getByRole('listbox')).toHaveCount(0);
  await page.locator('.hl-inspector-sidebar').getByRole('tab',{name:'Runs',exact:true}).click();
  await page.locator('.hl-inspector-sidebar').getByRole('tab',{name:'Inspector',exact:true}).click();
  await expect(numeric).toHaveText('2▾');
  // Force the anchor near the viewport edge to exercise flip and narrow placement.
  await mode.evaluate(el=>Object.assign((el as HTMLElement).style,{position:'fixed',bottom:'12px',right:'12px',width:'150px',zIndex:'13000'}));
  await mode.click();
  const lower=(await mode.boundingBox())!, upper=(await page.getByRole('listbox').boundingBox())!;
  expect(upper.y+upper.height).toBeLessThanOrEqual(lower.y);
  await page.screenshot({path:'/tmp/enum-custom/flipped.png',clip:{x:1050,y:700,width:390,height:350}});
  await mode.press('Escape');
  await mode.evaluate(el=>{for(const key of ['position','bottom','right','width','z-index'])(el as HTMLElement).style.removeProperty(key);});
  await page.setViewportSize({width:390,height:700});
  await page.getByRole('tab',{name:'Inspect',exact:true}).click();
  await mode.click();
  await expect.poll(async()=>{const r=(await page.getByRole('listbox').boundingBox())!;return r.x+r.width<=390;}).toBe(true);
  await page.screenshot({path:'/tmp/enum-custom/narrow.png'});
  await mode.press('Escape');
  await page.setViewportSize({width:1440,height:1050});

 }
 mkdirSync('/tmp/enum-screens',{recursive:true});
 for(const theme of ['light','dark']){
  await page.evaluate(t=>document.documentElement.dataset.theme=t,theme);
    await page.waitForTimeout(250);
  await panel.screenshot({path:`/tmp/enum-screens/${legacy?'legacy':'select'}-${theme}.png`});
 }
 if(!legacy){
  await page.getByRole('button',{name:'运行流程',exact:true}).click();
  await expect.poll(()=>dispatched?.nodes[0].params.numeric).toBe('2');
  expect(dispatched.nodes[0].params.motion_mask_mode).toBe('gmc_residual');
 }
});
