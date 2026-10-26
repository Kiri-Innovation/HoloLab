import {test,expect} from '@playwright/test';
for(const bridge of [false,true]) test(`package source lives in Pack Manager and keeps suffixes (bridge=${bridge})`,async({page})=>{
 const target='/cloud/实验项目/cloud-ssd1/a-very-long-workspace-directory/packs/fastgs-silhouette-train@0.1.0/manifest.yaml';
 if(bridge) await page.addInitScript(()=>{(window as any).flops={version:1,showDocument:async(args:any)=>{(window as any).__opened=args;return {success:true};}};});
 const pack={manifest_path:target,name:'stg-train',version:'0.3.0',manifest_hash:'enum',node_ids:['compute'],inputs:{},outputs:{},params:{}};
 await page.route('**/api/**',async route=>{
  const path=new URL(route.request().url()).pathname;let body:any=[];
  if(path==='/api/pack-catalog')body=[pack];
  else if(path==='/api/nodes')body=[{node_id:'compute',node_name:'Compute',flops_executor_id:'device',packs:[pack],gpu:{count:0},connected_ts:1}];
  else if(path==='/api/capabilities/check')body={nodes:{'kiri/stg-train@0.3.0':{status:'undeclared',issues:[]}}};
  await route.fulfill({json:body});
 });
 await page.setViewportSize({width:1440,height:1050});await page.goto('/packs');
 await page.getByText('安装与源码',{exact:true}).click();
 const row=page.locator('[data-hl-open-source]');
 await expect(row).toHaveAttribute('title',target);
 const label=row.locator('[data-hl-package-path] > span').first();
 await expect(label).toContainText('fastgs-silhouette-train@0.1.0');
 await row.evaluate(el=>(el as HTMLElement).style.width='230px');
 await expect(label).toHaveText(/^….+@0\.1\.0$/);
 await expect.poll(()=>label.evaluate(el=>el.getBoundingClientRect().width<=el.parentElement!.clientWidth)).toBe(true);
 if(bridge){await row.click();await expect.poll(()=>page.evaluate(()=>(window as any).__opened?.path)).toBe(target);}
 else {await page.setViewportSize({width:390,height:700});await expect(label).toContainText('@0.1.0');await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);}
});
