import {expect,test} from '@playwright/test';
import {readFileSync} from 'node:fs';
const live=process.env.HOLOLAB_RESOURCE_SAMPLES ? JSON.parse(readFileSync(process.env.HOLOLAB_RESOURCE_SAMPLES,'utf8')) : null;
const gpu={index:0,uuid:'gpu-a',name:'GPU',mem_used_mb:400,mem_total_mb:24576,util_percent:10};
const samples=live ?? Object.fromEntries(['cpu','mem','gpu','vram'].map(metric=>[metric,{metric,ts:1,error:null,gpus:[gpu],total_percent:40,total_used_mb:1024,total_capacity_mb:4096,sample_window_s:.3,processes:[{pid:12345,name:'python',gpu_uuid:'gpu-a',used_mb:400,usage_percent:10,owner:'external',cmdline:'python test.py',termination_token:'t'}]}]));
test('four metric lenses, read-only host menus, return focus and narrow layout',async({page})=>{
  page.on('pageerror',e=>{throw e;});
  await page.routeWebSocket('**',ws=>ws.close());
  const calls:string[]=[];
  await page.route('**/api/**',async route=>{
    const path=new URL(route.request().url()).pathname;
    let body:unknown=[];
    if(path.includes('/resources/')){const metric=path.split('/').at(-1)!;calls.push(metric);body=samples[metric];}
    else if(path==='/api/nodes')body=[{node_id:'node-a',node_name:live?'Local host · recorded sample':'Test device',gpu:{count:1,name:'GPU',total_vram_gb:24},packs:[],connected_ts:0,advertised_url:null,workspace_root:null,legacy_workspace_roots:[],flops_executor_id:null,packs_dir:null}];
    else if(path==='/api/nodes/metrics/history')body={sample_interval_s:3,nodes:{'node-a':[{ts:1,cpu_percent:samples.cpu.total_percent,mem_used_gb:samples.mem.total_used_mb/1024,mem_total_gb:samples.mem.total_capacity_mb/1024,gpus:samples.gpu.gpus}]}};
    else if(path==='/api/workflows/lens-test')body={workflow_id:'lens-test',name:'Resource metrics preview',graph:{nodes:[],edges:[]},created_ts:1,updated_ts:1};
    await route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
  });
  await page.setViewportSize({width:1440,height:1000});
  await page.addInitScript(()=>localStorage.setItem('hl-layout-bottom','540'));
  await page.goto('/w/lens-test');
  const panel=page.locator('.hl-node-metrics');
  for(const metric of ['cpu','mem','gpu','vram']){
    const label=metric.toUpperCase();
    await panel.getByRole('button',{name:`Open ${label} resource lens`,exact:true}).click();
    const lens=page.getByRole('region',{name:`${label} resource lens`});
    await expect(lens).not.toHaveAttribute('aria-busy','true');
    expect(calls).toContain(metric);
    await expect(lens.locator('.hl-resource-process-entry')).toHaveCount(samples[metric].processes.length);
    if(metric==='cpu'||metric==='mem'){
      await lens.getByRole('button',{name:/进程菜单/}).first().click();
      await expect(page.getByRole('menuitem',{name:'结束进程',exact:true})).toHaveCount(0);
      await expect(page.getByRole('menuitem',{name:'复制 PID'})).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(lens).toBeVisible();
    }
    for(const theme of ['light','dark']){
      await page.evaluate(t=>document.documentElement.dataset.theme=t,theme);
      await page.waitForTimeout(300);
      await panel.screenshot({path:`test-results/resource-${metric}-${live?'live':'fixture'}-${theme}.png`});
    }
    await lens.getByRole('button',{name:'All compute nodes',exact:false}).click();
    await expect(panel.getByRole('button',{name:`Open ${label} resource lens`,exact:true})).toBeFocused();
  }
  await page.setViewportSize({width:390,height:844});
  await page.getByRole('tab',{name:'Inspect',exact:true}).click();
  await panel.getByRole('button',{name:'Open MEM resource lens'}).click();
  const lens=page.getByRole('region',{name:'MEM resource lens'});
  await expect(lens).not.toHaveAttribute('aria-busy','true');
  expect(await panel.evaluate(el=>el.scrollWidth<=el.clientWidth)).toBe(true);
  await lens.locator('header').scrollIntoViewIfNeeded();
  await page.screenshot({path:'test-results/resource-mem-mobile.png'});
});
