import {test,expect} from '@playwright/test';
import {mkdirSync} from 'node:fs';
test('inspector sections share a scroller and preserve position across tabs',async({page})=>{
 const legacy=false;
 const pack = {description:'Train a spatiotemporal model.',docs:'## Training details\n'+('Training configuration and resource usage.\n\n'.repeat(12)),name:'stg-train',version:'0.3.0',manifest_hash:'enum',node_ids:['compute'],inputs:{},outputs:{},params:{
  motion_mask_mode:{type:'enum',default:'depth_p10p90',values:['gmc_residual','disp_p10p90','depth_p10p90']},
  numeric:{type:'enum',default:1,values:[1,2,'2']},
  missing:{type:'enum',default:'original'}, ...Object.fromEntries(Array.from({length:40},(_,i)=>[`setting_${i}`,{type:'float',default:0.5,description:'Editable parameter.'}]))
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

 const scroller=page.locator('.hl-inspector-sidebar .hl-sidebar-content');
 const tabs=page.locator('.hl-inspector-sidebar [role=tablist]');
 await panel.getByRole('button',{name:'Read More',exact:true}).click();
 await expect(panel.getByRole('region',{name:'Pack documentation'})).toBeVisible();
 const initial=await panel.getByRole('region',{name:'Node overview'}).boundingBox();
 const tabTop=(await tabs.boundingBox())!.y;
 const total=await scroller.evaluate(el=>el.scrollHeight-el.clientHeight);
 expect(total).toBeGreaterThan(700);
 expect(await panel.locator('.hl-inspector-parameters').evaluate(el=>getComputedStyle(el).overflowY)).toBe('visible');
 mkdirSync('/tmp/inspector-scroll',{recursive:true});
 for(const theme of ['light','dark']){
  await page.evaluate(t=>document.documentElement.dataset.theme=t,theme);
  await page.waitForTimeout(250);
  for(const [label,position] of [['top',0],['middle',total/2],['bottom',total]] as const){
   await scroller.evaluate((el,y)=>el.scrollTop=y,position);
   await expect.poll(async()=>(await tabs.boundingBox())!.y).toBe(tabTop);
   expect((await panel.getByRole('region',{name:'Node overview'}).boundingBox())!.y).toBeCloseTo(initial!.y-(await scroller.evaluate(el=>el.scrollTop)),0);
   await page.locator('.hl-inspector-sidebar').screenshot({path:`/tmp/inspector-scroll/${theme}-${label}.png`});
  }
 }
 const before=await scroller.evaluate(el=>el.scrollTop);
 await tabs.getByRole('tab',{name:'Runs',exact:true}).click();
 await tabs.getByRole('tab',{name:'Inspector',exact:true}).click();
 expect(await scroller.evaluate(el=>el.scrollTop)).toBe(before);
 const last=panel.getByLabel('setting_39',{exact:true});
 await last.click(); await last.press('ControlOrMeta+A'); await last.pressSequentially('42');
 await expect.poll(()=>saved.at(-1)?.graph.nodes[0].params.setting_39).toBe(42);
 await scroller.evaluate(el=>el.scrollTop=0);
 await panel.getByRole('button',{name:'Show Less',exact:true}).click();
 expect(await scroller.evaluate(el=>el.scrollHeight-el.clientHeight)).toBeLessThan(total);
 await mode.click();
 let anchor=(await mode.boundingBox())!, popup=(await page.getByRole('listbox').boundingBox())!;
 expect(popup.y).toBeGreaterThanOrEqual(anchor.y+anchor.height);
 await scroller.evaluate(el=>el.scrollTop=40);
 await expect.poll(async()=>{anchor=(await mode.boundingBox())!;popup=(await page.getByRole('listbox').boundingBox())!;return Math.abs(popup.y-anchor.y-anchor.height-4);}).toBeLessThan(1);
 await mode.press('Escape');
 await page.locator('.react-flow__pane').click({position:{x:5,y:5}});
 await expect(tabs.getByRole('tab',{name:'Runs',exact:true})).toHaveAttribute('aria-selected','true');
 await page.locator('[data-hololab-node="algorithm"]').click();
 await expect(tabs.getByRole('tab',{name:'Inspector',exact:true})).toHaveAttribute('aria-selected','true');
 await page.setViewportSize({width:390,height:700});
 await page.getByRole('tab',{name:'Inspect',exact:true}).click();
 await page.locator('.hl-mobile-inspector').evaluate(el=>el.scrollTop=el.scrollHeight);
 await expect(last).toBeVisible();
 await page.screenshot({path:'/tmp/inspector-scroll/mobile-bottom.png'});
});
