import {expect, test} from '@playwright/test';
import {readFileSync} from 'node:fs';
const evidence = process.env.HOLOLAB_TERMINATION_EVIDENCE ? JSON.parse(readFileSync(process.env.HOLOLAB_TERMINATION_EVIDENCE,'utf8')) : null;
const gpu={index:0,uuid:'gpu',name:'GPU',mem_used_mb:512,mem_total_mb:24576};
const target={pid:12345,name:'python',owner:'external',used_mb:400,gpu_uuid:'gpu',cmdline:'python temporary-test.py --test-only',termination_token:'one-use'};
const before=evidence?.before ?? {ts:1,error:null,gpus:[gpu],processes:[target]};
const after=evidence?.after ?? {...before,processes:[],gpus:[{...gpu,mem_used_mb:112}]};
const targetProcess=before.processes[0];

test('external confirmation, cancel, success refresh, themes and job cancel',async({page})=>{
  page.on('pageerror',e=>{throw e;});
  let terminated=false; let posts=0; let cancels=0; let reject=true;
  await page.routeWebSocket('**',ws=>ws.close());
  await page.route('**/api/**',async route=>{
    const path=new URL(route.request().url()).pathname;
    let body:any=[];
    if(path.endsWith('/resources/processes/terminate')){
      expect(route.request().postDataJSON()).toEqual({pid:targetProcess.pid,token:targetProcess.termination_token});
      posts++;
      if(reject) return route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({detail:'Process changed since confirmation; refresh the lens'})});
      terminated=true;body=evidence?.result ?? {status:'terminated'};
    }else if(path.endsWith('/resources/vram'))body=terminated ? after : before;
    else if(path==='/api/nodes')body=[{node_id:'node-a',node_name:evidence ? 'Temporary GPU test · recorded real sample' : 'Resource lens · test fixture',gpu:{count:1,name:before.gpus[0].name,total_vram_gb:24},packs:[],connected_ts:0,advertised_url:null,workspace_root:null,legacy_workspace_roots:[],flops_executor_id:null,packs_dir:null}];
    else if(path==='/api/nodes/metrics/history')body={sample_interval_s:3,nodes:{'node-a':[{ts:1,cpu_percent:0,mem_used_gb:1,mem_total_gb:64,gpus:before.gpus}]}};
    else if(path==='/api/workflows/lens-test')body={workflow_id:'lens-test',name:'Resource lens test',graph:{nodes:[],edges:[]},created_ts:1,updated_ts:1};
    else if(path.endsWith('/cancel')){cancels++;body={};}
    await route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
  });
  await page.setViewportSize({width:1440,height:1000});
  await page.addInitScript(()=>localStorage.setItem('hl-layout-bottom','500'));
  await page.goto('/w/lens-test');
  await page.getByRole('button',{name:'Open VRAM resource lens'}).click();
  const lens=page.getByRole('region',{name:'VRAM resource lens'});
  await page.context().grantPermissions(['clipboard-read','clipboard-write']);
  const openMenu=()=>lens.getByRole('button',{name:`进程菜单 · PID ${targetProcess.pid}`,exact:true}).click();
  await openMenu();
  for(const theme of ['light','dark']){
    await page.evaluate(theme=>document.documentElement.dataset.theme=theme,theme);
    await page.waitForTimeout(300); // Let theme color transitions settle before capturing.
    await page.screenshot({path:`test-results/process-menu-${theme}.png`});
  }
  await page.getByRole('menuitem',{name:'复制 PID',exact:true}).click();
  expect(await page.evaluate(()=>navigator.clipboard.readText())).toBe(String(targetProcess.pid));
  await expect(lens).toContainText('已复制 PID');
  await openMenu();
  await page.getByRole('menuitem',{name:'复制引用',exact:true}).click();
  await expect(lens).toContainText('已复制引用');
  const copied=await page.evaluate(()=>navigator.clipboard.readText());
  expect(copied).toContain(`${targetProcess.name} (PID ${targetProcess.pid})`);
  expect(copied).toContain('Compute node: node-a');
  expect(copied).toContain('/w/lens-test');
  console.log('clipboard reference:',copied);
  await openMenu();await page.keyboard.press('Escape');
  await expect(page.getByRole('menu')).toHaveCount(0);await expect(lens).toBeVisible();

  await lens.getByRole('button',{name:`进程菜单 · PID ${targetProcess.pid}`,exact:true}).click();
  await page.getByRole('menuitem',{name:'结束进程',exact:true}).click();
  const dialog=page.getByRole('dialog');
  await expect(dialog.locator('pre')).toHaveText(targetProcess.cmdline);
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);await expect(lens).toBeVisible();expect(posts).toBe(0);
  await lens.getByRole('button',{name:`进程菜单 · PID ${targetProcess.pid}`,exact:true}).click();
  await page.getByRole('menuitem',{name:'结束进程',exact:true}).click();
  await dialog.getByRole('button',{name:'结束进程',exact:true}).click();
  await expect(dialog.getByRole('alert')).toContainText('Process changed');
  await expect(dialog.getByRole('button',{name:'结束进程',exact:true})).toBeDisabled();
  await dialog.getByRole('button',{name:'Close',exact:true}).click();
  reject=false;
  await lens.getByRole('button',{name:`进程菜单 · PID ${targetProcess.pid}`,exact:true}).click();
  await page.getByRole('menuitem',{name:'结束进程',exact:true}).click();
  for(const theme of ['light','dark']){
    await page.evaluate(theme=>document.documentElement.dataset.theme=theme,theme);
    await page.waitForTimeout(300);
    await dialog.screenshot({path:`test-results/terminate-confirm-${theme}.png`});
  }
  await dialog.evaluate(el=>(el as HTMLElement).style.width='358px');
  expect(await dialog.evaluate(el=>el.scrollWidth<=el.clientWidth)).toBe(true);
  await dialog.screenshot({path:'test-results/terminate-confirm-mobile.png'});
  await dialog.evaluate(el=>(el as HTMLElement).style.width='');
  await dialog.getByRole('button',{name:'结束进程',exact:true}).click();
  await expect(dialog).toHaveCount(0);
  await expect(lens).toContainText('terminated (SIGTERM)');
  await expect(lens).toContainText('No compute processes');
  expect(posts).toBe(2);
  for(const theme of ['light','dark']){
    await page.evaluate(theme=>document.documentElement.dataset.theme=theme,theme);
    await page.locator('.hl-node-metrics').screenshot({path:`test-results/terminate-refreshed-${theme}.png`});
  }
  // Own jobs never receive direct termination controls.
  after.processes=[{...targetProcess,owner:'job',job_id:'job-a',job_name:'test job'}];
  await lens.getByRole('button',{name:'Refresh',exact:true}).click();
  await expect(lens.getByRole('button',{name:'结束进程…',exact:true})).toHaveCount(0);
  await lens.getByRole('button',{name:'停止任务',exact:true}).click();
  await expect(lens).toContainText('Task cancellation requested');expect(cancels).toBe(1);
});
