import { test, expect } from '@playwright/test';
for (const mode of ['legacy', 'alias', 'dev'] as const) {
  test(`package identity ${mode}: display and save`, async ({page}) => {
    const name=mode==='dev'?'scope/example':'kiri/example';
    const version=mode==='dev'?'0.1.1+':'0.1.0';
    const recorded=mode==='legacy'?'example':name;
    const registered=mode==='dev'?name:'example';
    const pack={name,version,manifest_hash:'test',description:'Identity compatibility',inputs:{},outputs:{},params:{count:{type:'int',default:12}},node_ids:['compute'],arrayable:false};
    const graph={nodes:[{id:'a',kind:'algorithm',algorithm_name:recorded,algorithm_version:version,position:{x:100,y:100},params:{count:12},assigned_node_id:'compute'}],edges:[]};
    await page.routeWebSocket('**',()=>{});
    await page.route('**/api/**', async route => {
      const request=route.request(),path=new URL(request.url()).pathname;
      let body:any=[];
      if(path==='/api/pack-catalog')body=[pack];
      else if(path==='/api/nodes')body=[{node_id:'compute',node_name:'Compute',packs_dir:'/legacy/packs',gpu:{count:0},packs:[{...pack,name:registered}]}];
      else if(path==='/api/nodes/metrics/history')body={nodes:{}};
      else if(path==='/api/workflows/identity')body={workflow_id:'identity',name:'Package identity',graph,updated_ts:1,created_ts:1};
      else if(path==='/api/workflows' && request.method()==='POST')body={workflow_id:'identity',name:'Package identity',graph:request.postDataJSON().graph,updated_ts:2,created_ts:1};
      else if(path.endsWith('/analyze'))body={revision:'current',graph:request.postDataJSON().graph,staleness:{a:null},diff:[],environment_params:{}};
      await route.fulfill({json:body});
    });
    await page.setViewportSize({width:1440,height:1000});
    await page.goto('/w/identity');
    await page.locator('[data-hololab-node="algorithm"]').click();
    const inspector=page.locator('.hl-node-inspector');
    await expect(inspector.getByRole('spinbutton',{name:'count',exact:true})).toHaveValue('12');
    if(mode==='dev')await expect(inspector.getByLabel('Development version')).toBeVisible();
    await expect(page.getByText('No online compute node offers this pack.')).toHaveCount(0);
    await expect(inspector.locator('[data-hl-open-source]')).toHaveCount(0);
    const saved=page.waitForRequest(r=>new URL(r.url()).pathname==='/api/workflows' && r.method()==='POST' && r.postDataJSON().graph.nodes[0].params.count===13);
    await inspector.getByRole('spinbutton',{name:'count',exact:true}).fill('13');
    await page.locator('.hl-recipe-menu > summary').click();
    await page.getByRole('button',{name:'保存配方文件',exact:true}).click();
    const request=await saved;
    expect(request.postDataJSON().graph.nodes[0].algorithm_name).toBe(recorded);
    expect(request.postDataJSON().graph.nodes[0].algorithm_version).toBe(version);
    await page.screenshot({path:`/tmp/hololab-package-identity-${mode}.png`});
  });
}
