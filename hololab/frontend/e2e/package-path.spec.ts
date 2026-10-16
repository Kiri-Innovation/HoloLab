import {test,expect} from '@playwright/test';
import {mkdirSync} from 'node:fs';
for(const bridge of [false,true]) test(`package path keeps whole trailing segments (bridge=${bridge})`,async({page})=>{
 const legacy=false;
 const target='/cloud/实验项目/cloud-ssd1/a-very-long-workspace-directory/packs/fastgs-silhouette-train@0.1.0/manifest.yaml';
 if(bridge) await page.addInitScript(()=>{(window as any).flops={version:1,showDocument:async(args:any)=>{(window as any).__opened=args;return {success:true};}};});
 const pack = {manifest_path:target,name:'stg-train',version:'0.3.0',manifest_hash:'enum',node_ids:['compute'],inputs:{},outputs:{},params:{
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
  else if(path==='/api/nodes')body=[{node_id:'compute',node_name:'Compute',flops_executor_id:'device',packs:[pack],gpu:{count:0},connected_ts:1}];
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

 await expect(panel.getByRole('region',{name:'Package',exact:true})).toHaveCount(0);
 await expect(panel.locator('.hl-inspector-field').first()).toContainText('package');
 await expect(panel.locator('.hl-inspector-package-field .hl-parameter-type')).toHaveText('path');
 const row=panel.locator('[data-hl-open-source]');
 const label=row.locator('[data-hl-package-path] > span').first();
 await expect(label).toHaveText('…/manifest.yaml');
 await panel.locator('.hl-inspector-package-field').evaluate(el=>(el as HTMLElement).style.width='680px');
 await expect(label).toContainText('fastgs-silhouette-train@0.1.0/manifest.yaml');
 await panel.locator('.hl-inspector-package-field').evaluate(el=>(el as HTMLElement).style.width='');
 await expect(row).toHaveAttribute('title',target);
 expect(await label.evaluate(el=>el.getBoundingClientRect().width<=el.parentElement!.clientWidth)).toBe(true);
 mkdirSync('/tmp/package-path',{recursive:true});
 for(const theme of ['light','dark']){
  await page.evaluate(t=>document.documentElement.dataset.theme=t,theme);await page.waitForTimeout(250);
  await panel.getByRole('region',{name:'Parameters',exact:true}).screenshot({path:`/tmp/package-path/${bridge?'clickable':'readonly'}-${theme}.png`});
 }
 await panel.locator('.hl-inspector-package-field').evaluate(el=>(el as HTMLElement).style.width='300px');
 await expect(label).toHaveText('…/manifest.yaml');
 expect(await label.evaluate(el=>el.getBoundingClientRect().width<=el.parentElement!.clientWidth)).toBe(true);
 await panel.locator('.hl-inspector-package-field').screenshot({path:`/tmp/package-path/narrow-${bridge}.png`});
 if(bridge){
  await row.click();await expect.poll(()=>page.evaluate(()=>(window as any).__opened?.path)).toBe(target);
 } else {
  await panel.locator('.hl-inspector-package-field').evaluate(el=>(el as HTMLElement).style.width='');
  await page.setViewportSize({width:390,height:700});await page.getByRole('tab',{name:'Inspect',exact:true}).click();
  await expect(label).toContainText('manifest.yaml');
  await page.screenshot({path:'/tmp/package-path/mobile.png'});
 }
});
