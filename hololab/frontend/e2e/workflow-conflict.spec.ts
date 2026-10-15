import {expect,test, type WebSocketRoute} from '@playwright/test';
const message="This workflow was modified in another tab (or by an agent) after you opened it. Reload the page to see the fresh version; your unsaved edits will be lost. (Save is parked until you reload to protect the other writer's changes.)";
for(const action of ['reload','dismiss'] as const){
  test(`two tabs: conflict dialog ${action} preserves parked saves`,async({context,page})=>{
    let server={workflow_id:'conflict-test',name:'Original',graph:{nodes:[],edges:[]},created_ts:1,updated_ts:1};
    let posts=0;let nativeDialogs=0;
    const sockets: WebSocketRoute[] = [];
    await context.routeWebSocket('**',ws => { sockets.push(ws); });
    await context.route('**/api/**',async route=>{
      const req=route.request();const path=new URL(req.url()).pathname;
      let body:unknown=[];
      if(path==='/api/workflows' && req.method()==='POST'){
        posts++;const draft=req.postDataJSON();
        if(draft.base_updated_ts!==server.updated_ts){
          return route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({detail:{code:'workflow_conflict',message:'workflow was modified by another writer',base_updated_ts:draft.base_updated_ts,current:server}})});
        }
        server={...server,...draft,updated_ts:server.updated_ts+1};body=server;
      }else if(path==='/api/workflows/conflict-test')body=server;
      else if(path==='/api/pack-catalog')body=[{name:'demo',version:'1',manifest_hash:'x',node_ids:[],inputs:{},outputs:{},params:{},arrayable:false}];
      else if(path==='/api/nodes/metrics/history')body={sample_interval_s:3,nodes:{}};
      await route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
    });
    page.on('dialog',async d=>{nativeDialogs++;await d.dismiss();});
    page.on('pageerror',e=>{throw e;});
    await page.setViewportSize({width:1280,height:900});
    await page.goto('/w/conflict-test');
    await expect(page.getByPlaceholder('workflow name')).toHaveValue('Original');
    const other=await context.newPage();
    await other.goto('/w/conflict-test');
    await expect(other.getByPlaceholder('workflow name')).toHaveValue('Original');
    // A second tab writes through the API; the mocked server broadcasts the
    // same notification an agent edit would send while tab A is dirty.
    await page.waitForTimeout(500); // Let async graph hydration settle before editing.
    await page.bringToFront();
    await page.getByPlaceholder('workflow name').fill('Unsaved tab A edits');
    await expect(page.locator('[data-hl-save-status="unsaved"]')).toBeVisible();
    await other.evaluate(async () => {
      await fetch('/api/workflows', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({workflow_id:'conflict-test',name:'New version from tab B',base_updated_ts:1})});
    });
    expect(server.updated_ts).toBe(2);
    for (const socket of sockets) socket.send(JSON.stringify({kind:'workflow_updated',payload:{workflow_id:server.workflow_id,name:server.name,updated_ts:server.updated_ts,origin:'other-writer'}}));
    const dialog=page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    await expect(dialog.locator('p')).toHaveText(message);
    await expect(page.locator('[data-hl-save-status="conflict"]')).toBeVisible();
    expect(nativeDialogs).toBe(0);
    if(action==='dismiss'){
      await dialog.getByRole('button',{name:'暂不刷新',exact:true}).click();
      await expect(dialog).toHaveCount(0);
      await page.getByPlaceholder('workflow name').fill('More local edits while parked');
      await page.waitForTimeout(1200); // Longer than the 800ms autosave debounce.
      expect(posts).toBe(1);
      expect(server.name).toBe('New version from tab B');
      await expect(page.locator('[data-hl-save-status="conflict"]')).toBeVisible();
    }else{
      for(const theme of ['light','dark']){
        await page.evaluate(t=>document.documentElement.dataset.theme=t,theme);
        await page.waitForTimeout(250);
        await page.screenshot({path:`test-results/workflow-conflict-${theme}.png`});
      }
      await Promise.all([page.waitForEvent('load'),dialog.getByRole('button',{name:'重新加载',exact:true}).click()]);
      await expect(page.getByPlaceholder('workflow name')).toHaveValue('New version from tab B');
      await expect(page.getByRole('dialog')).toHaveCount(0);
      await page.waitForTimeout(1200);
      expect(posts).toBe(1); // No unload beacon or retry overwrote the other tab.
    }
    await other.close();
  });
}
