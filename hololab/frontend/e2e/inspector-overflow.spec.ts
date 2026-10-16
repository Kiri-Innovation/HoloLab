import {test,expect} from '@playwright/test';
import {mkdirSync,readFileSync,writeFileSync} from 'node:fs';
test('inspector horizontal overflow audit',async({page})=>{

 let pack:any = {manifest_path:'/cloud/very-long-installation-directory/SpacetimeGaussians/stg-train@0.3.0/manifest.yaml',description:'Train a model.',docs:'Documentation. '.repeat(100),name:'stg-train',version:'0.3.0',manifest_hash:'enum',node_ids:['compute'],inputs:{},outputs:{},params:{
  motion_mask_mode:{description:'Select the motion mask method.',type:'enum',default:'depth_p10p90',values:['gmc_residual','disp_p10p90','depth_p10p90']},
  numeric:{type:'enum',default:1,values:[1,2,'2']},
  missing:{type:'enum',default:'original'}
 }};
 if(process.env.OVERFLOW_PACK)pack=JSON.parse(readFileSync(process.env.OVERFLOW_PACK,'utf8'));
 pack.node_ids=['compute'];
 let graph:any={nodes:[{id:'stg',algorithm_name:pack.name,algorithm_version:pack.version,position:{x:100,y:100},params:{},assigned_node_id:'compute',arrayed_toggle:false}],edges:[]};
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


 const reports:any[]=[];
 const outputPrefix=process.env.OVERFLOW_PACK ? (process.env.OVERFLOW_EXPECT_CLEAN ? 'after' : 'before') : 'regression';
 const sidebar=page.locator('.hl-inspector-sidebar');
 const measure=()=>sidebar.evaluate(root=>{
  const scroller=root.querySelector('.hl-sidebar-content')!;
  const bound=scroller.getBoundingClientRect();
  return {document:{client:document.scrollingElement!.clientWidth,scroll:document.scrollingElement!.scrollWidth},inspector:{client:scroller.clientWidth,scroll:scroller.scrollWidth},elements:Array.from(root.querySelectorAll('*')).flatMap(el=>{
   const r=el.getBoundingClientRect();if(r.width===0||r.right<=bound.right+0.5)return [];
   const style=getComputedStyle(el);
   return [{selector:el.tagName.toLowerCase()+(el.className?'.'+String(el.className).trim().split(/\s+/).join('.'):''),text:el.textContent?.slice(0,100),over:Math.round((r.right-bound.right)*100)/100,visibility:style.visibility,position:style.position}];
  })};
 });
 mkdirSync('/tmp/inspector-overflow',{recursive:true});
 for(const width of [380,280,180]){
  await sidebar.evaluate((el,w)=>(el.parentElement as HTMLElement).style.gridTemplateColumns=`300px 6px minmax(0,1fr) 6px ${w}px`,width);
  for(const expanded of [false,true]){
   if(expanded&&await panel.getByRole('button',{name:'Read More',exact:true}).count())await panel.getByRole('button',{name:'Read More',exact:true}).click();
   await page.waitForTimeout(250);
   const report={width,expanded,...await measure()};reports.push(report);
   if(process.env.OVERFLOW_EXPECT_CLEAN || !process.env.OVERFLOW_PACK){expect(report.inspector.scroll).toBe(report.inspector.client);expect(report.document.scroll).toBe(report.document.client);expect(report.elements).toEqual([]);}
   if(!expanded){
    const help=panel.locator('.hl-parameter-help').first();
    await help.hover();
    await expect(page.getByRole('tooltip')).toBeVisible();
    const hover=await measure();
    expect(hover.inspector.scroll).toBe(hover.inspector.client);
    await page.mouse.move(0,0);
   }
   for(const theme of ['light','dark']){
    await page.evaluate(t=>document.documentElement.dataset.theme=t,theme);await page.waitForTimeout(150);
    await sidebar.screenshot({path:`/tmp/inspector-overflow/${outputPrefix}-${width}-${expanded}-${theme}.png`});
   }
   if(expanded&&await panel.getByRole('button',{name:'Show Less',exact:true}).count())await panel.getByRole('button',{name:'Show Less',exact:true}).click();
  }
 }
 writeFileSync(`/tmp/inspector-overflow/${outputPrefix}.json`,JSON.stringify(reports,null,2));
});
