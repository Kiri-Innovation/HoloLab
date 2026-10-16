import {test,expect} from '@playwright/test';
import {mkdirSync} from 'node:fs';
test('F returns to the Inspector node without interrupting inputs',async({page})=>{
const legacy=false;
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

 mkdirSync('/tmp/frame-selected',{recursive:true});
 const node=page.locator('[data-hololab-node="algorithm"]');
 const canvas=page.locator('.react-flow').first();
 const transform=()=>page.locator('.react-flow__viewport').evaluate(el=>getComputedStyle(el).transform);
 await page.screenshot({path:'/tmp/frame-selected/selected.png'});
 const area=(await canvas.boundingBox())!;
 await page.mouse.move(area.x+area.width/2,area.y+area.height/2);
 await page.mouse.down({button:'middle'});
 await page.mouse.move(area.x+area.width/2+900,area.y+area.height/2+400,{steps:12});
 await page.mouse.up({button:'middle'});
 await expect(page.getByRole('tab',{name:'Inspector',exact:true})).toHaveAttribute('aria-selected','true');
 await page.screenshot({path:'/tmp/frame-selected/panned.png'});
 const before=await transform();
 await panel.getByLabel('missing',{exact:true}).fill('f');
 await panel.getByLabel('missing',{exact:true}).press('f');
 await mode.focus();await mode.press('f');
 await panel.getByLabel('Assigned node').focus();await page.keyboard.press('f');
 await page.evaluate(()=>{const e=document.createElement('div');e.contentEditable='true';e.id='typing-test';document.body.append(e);e.focus();});
 await page.keyboard.press('f');await page.locator('#typing-test').evaluate(el=>el.remove());
 await page.keyboard.press('Shift+F');
 await page.waitForTimeout(300);expect(await transform()).toBe(before);
 await page.evaluate(()=>(document.activeElement as HTMLElement)?.blur());
 await page.keyboard.press('f');
 await expect.poll(async()=>{const r=(await node.boundingBox())!, c=(await canvas.boundingBox())!;return Math.abs(r.x+r.width/2-c.x-c.width/2)+Math.abs(r.y+r.height/2-c.y-c.height/2);}).toBeLessThan(3);
 const framed=await transform();
 const zoom=await page.locator('.react-flow__viewport').evaluate(el=>new DOMMatrix(getComputedStyle(el).transform).a);
 expect(zoom).toBeGreaterThanOrEqual(0.05);expect(zoom).toBeLessThanOrEqual(2);
 await page.screenshot({path:'/tmp/frame-selected/framed.png'});
 await page.keyboard.press('f');await page.waitForTimeout(350);expect(await transform()).toBe(framed);
 // With selection cleared, F is inert.
 await canvas.locator('.react-flow__pane').click({position:{x:5,y:5}});
 const empty=await transform();await page.keyboard.press('f');await page.waitForTimeout(300);expect(await transform()).toBe(empty);
});

test('frame selected respects both zoom limits',async({page})=>{
 await page.goto('/e2e/fixtures/frame-selected.html');
 const zoom=()=>page.locator('.react-flow__viewport').evaluate(el=>new DOMMatrix(getComputedStyle(el).transform).a);
 await page.getByRole('button',{name:'Small'}).click();await page.keyboard.press('f');
 await expect.poll(zoom).toBeCloseTo(1.4,3);
 await page.getByRole('button',{name:'Large'}).click();await page.keyboard.press('f');
 await expect.poll(zoom).toBeCloseTo(0.3,3);
});
