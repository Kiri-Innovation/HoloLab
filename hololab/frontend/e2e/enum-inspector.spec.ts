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
 await expect(mode).toHaveJSProperty('tagName',legacy?'INPUT':'SELECT');
 await expect(panel.getByLabel('missing',{exact:true})).toHaveValue('original');
 if(legacy){await expect(mode).toHaveValue('legacy');await expect(panel.getByText('Current value is not in the available options.')).toBeVisible();}
 else {
  await mode.selectOption('0');
  await panel.getByLabel('numeric',{exact:true}).selectOption({value:'1'});
  await expect.poll(()=>saved.at(-1)?.graph.nodes[0].params.numeric).toBe(2);
  expect(saved.at(-1).graph.nodes[0].params.motion_mask_mode).toBe('gmc_residual');
  await panel.getByLabel('numeric',{exact:true}).selectOption({value:'2'});
  await expect.poll(()=>saved.at(-1)?.graph.nodes[0].params.numeric).toBe('2');

 }
 mkdirSync('/tmp/enum-screens',{recursive:true});
 for(const theme of ['light','dark']){
  await page.evaluate(t=>document.documentElement.dataset.theme=t,theme);
  await panel.screenshot({path:`/tmp/enum-screens/${legacy?'legacy':'select'}-${theme}.png`});
 }
 if(!legacy){
  await page.getByRole('button',{name:'运行流程',exact:true}).click();
  await expect.poll(()=>dispatched?.nodes[0].params.numeric).toBe('2');
  expect(dispatched.nodes[0].params.motion_mask_mode).toBe('gmc_residual');
 }
});
