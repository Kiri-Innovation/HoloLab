import { test, expect } from '@playwright/test';

test('a failed environment stays local and whole-workflow blockers remain readable', async ({ page }) => {
  const pack = {name:'environment-test',version:'1.0.0',manifest_hash:'known',inputs:{},outputs:{},params:{},node_ids:['compute']};
  const graph = {nodes:['bad','good'].map((id,i)=>({id,kind:'algorithm',algorithm_name:pack.name,algorithm_version:pack.version,position:{x:i*400,y:0},params:{},assigned_node_id:'compute'})),edges:[]};
  const environment_checks = {bad:{status:'missing',source:'environment-test@1.0.0',issues:[{item:'package:absent-tool',required:'absent-tool',actual:'未安装',remedy:'安装后重试'}]},good:{status:'satisfied',issues:[]}};
  const blockers = ['bad (environment-test@1.0.0): package:absent-tool: 要求=absent-tool; 当前=未安装', 'second-bad: package:another-tool: 要求=another-tool; 当前=未安装'];
  let submissions=0;
  await page.routeWebSocket('**',()=>{});
  await page.route('**/api/**', async route=>{
    const path=new URL(route.request().url()).pathname;
    let body:any=[];
    if(path==='/api/pack-catalog') body=[pack];
    else if(path==='/api/nodes') body=[{node_id:'compute',node_name:'Compute',gpu:{count:0},packs:[pack]}];
    else if(path==='/api/nodes/metrics/history') body={nodes:{}};
    else if(path==='/api/workflows') body={workflow_id:'environment-check',name:'Environment check',updated_ts:1};
    else if(path==='/api/workflows/environment-check') body={workflow_id:'environment-check',name:'Environment check',graph,updated_ts:1,created_ts:1};
    else if(path.endsWith('/analyze')) body={revision:'current',graph,environment_checks,canvas_jobs:[{job_id:'done-job',graph_node_id:'good',state:'done',params:{},created_ts:1}],staleness:{good:null,bad:null},diff:[],environment_params:{}};
    else if(path.endsWith('/capabilities/check')) body={nodes:environment_checks};
    else if(path.endsWith('/prepare')) body={token:'blocked',run_blockers:blockers};
    else if(path.endsWith('/run')) { submissions++; body={}; }
    await route.fulfill({json:body});
  });
  await page.goto('/w/environment-check');
  const bad=page.locator('[data-id="bad"] [data-status-tone]');
  const good=page.locator('[data-id="good"] [data-status-tone]');
  await expect(bad).toHaveAttribute('data-status-tone','error');
  await expect(good).toHaveAttribute('data-status-tone','success');
  await bad.hover();
  await expect(page.locator('.hl-status-tooltip-content')).toContainText('要求=absent-tool; 当前=未安装');
  await page.getByRole('button',{name:'运行流程',exact:true}).click();
  const dialog=page.getByRole('dialog');
  await expect(dialog).toContainText(blockers[0]);
  await expect(dialog).toContainText(blockers[1]);
  expect(submissions).toBe(0);
});
