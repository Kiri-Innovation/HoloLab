import {test,expect} from '@playwright/test';
test('CPU and MEM confirm full command, protected rows are disabled, success refreshes',async({page})=>{
  page.on('pageerror',e=>{throw e;});
  const worker={pid:12345,name:'worker',cmdline:'python train.py --config /example/experiment.json',owner:'external',used_mb:1024,usage_percent:12.5,termination_token:'host-token'};
  const system={...worker,pid:12346,name:'sshd',cmdline:'/usr/sbin/sshd -D',termination_token:null,termination_reason:'系统关键进程，禁止终止：sshd'};
  let killed=false;let posts=0;
  await page.routeWebSocket('**',ws=>ws.close());
  await page.route('**/api/**',async route=>{
    const path=new URL(route.request().url()).pathname;let body:any=[];
    if(path.endsWith('/terminate')){expect(route.request().postDataJSON()).toEqual({pid:12345,token:'host-token'});killed=true;posts++;body={status:'terminated'};}
    else if(path.includes('/resources/'))body={metric:path.split('/').at(-1),ts:1,error:null,gpus:[],total_percent:20,total_used_mb:2000,total_capacity_mb:8000,processes:killed?[system]:[worker,system]};
    else if(path==='/api/nodes')body=[{node_id:'test',node_name:'Host test fixture',gpu:{count:0,name:null,total_vram_gb:null},packs:[],connected_ts:0,advertised_url:null,workspace_root:null,legacy_workspace_roots:[],flops_executor_id:null,packs_dir:null}];
    else if(path==='/api/nodes/metrics/history')body={sample_interval_s:3,nodes:{test:[{ts:1,cpu_percent:20,mem_used_gb:2,mem_total_gb:8,gpus:[]}]}};
    else if(path==='/api/workflows/lens-test')body={workflow_id:'lens-test',name:'Host test',graph:{nodes:[],edges:[]},created_ts:1,updated_ts:1};
    await route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
  });
  await page.setViewportSize({width:1440,height:1000});
  await page.addInitScript(()=>localStorage.setItem('hl-layout-bottom','540'));
  await page.goto('/w/lens-test');
  for(const metric of ['CPU','MEM']){
    killed=false;
    await page.getByRole('button',{name:`Open ${metric} resource lens`}).click();
    const lens=page.getByRole('region',{name:`${metric} resource lens`});
    await lens.getByRole('button',{name:'进程菜单 · PID 12346',exact:true}).click();
    await expect(page.getByRole('menuitem',{name:'结束进程',exact:true})).toBeDisabled();
    await expect(page.getByRole('menu')).toContainText('sshd');
    await page.keyboard.press('Escape');
    await lens.getByRole('button',{name:'进程菜单 · PID 12345',exact:true}).click();
    await page.getByRole('menuitem',{name:'结束进程',exact:true}).click();
    const dialog=page.getByRole('dialog');
    await expect(dialog.locator('pre')).toHaveText(worker.cmdline);
    await expect(dialog).toContainText(metric==='CPU'?'12.5%':'1.0 GiB');
    await dialog.screenshot({path:`test-results/host-${metric.toLowerCase()}-confirmation.png`});
    await dialog.getByRole('button',{name:'结束进程',exact:true}).click();
    await expect(lens).toContainText('terminated (SIGTERM)');
    await expect(lens.locator('.hl-resource-process-entry')).toHaveCount(1);
    await lens.getByRole('button',{name:'All compute nodes',exact:false}).click();
  }
  expect(posts).toBe(2);
});
