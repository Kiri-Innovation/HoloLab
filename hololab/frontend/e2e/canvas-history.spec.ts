import { test, expect } from '@playwright/test';

for (const olderRank of [2, 8, 9]) {
  test(`draft uses validated running job at history rank ${olderRank}`, async ({page}) => {
    const node = {id:'a', algorithm_name:'test', algorithm_version:'1.0.0', position:{x:100,y:100}, params:{}, assigned_node_id:'worker'};
    const graph = {nodes:[node],edges:[]};
    const pack = {name:'test',version:'1.0.0',manifest_hash:'hash',node_ids:['worker'],description:'test',category:[],inputs:{},outputs:{},params:{},arrayable:false};
    let valid = true;
    let state = 'running';
    const job = {job_id:'old-job',snapshot_id:'old',workflow_id:'history',node_id:'worker',graph_node_id:'a',algorithm_name:'test',algorithm_version:'1.0.0',params:{},input_handles:{},output_handles:null,parent_job_id:null,created_ts:1,updated_ts:1};
    const snapshots: string[] = [];
    await page.route('**/api/**', async route => {
      const path = new URL(route.request().url()).pathname;
      let body: unknown = {};
      if (path === '/api/pack-catalog') body = [pack];
      else if (path === '/api/nodes') body = [{node_id:'worker',node_name:'worker',packs:[pack],connected_ts:1,workspace_root:'/tmp',gpu:{total:0,gpus:[]}}];
      else if (path === '/api/workflows/history') body = {workflow_id:'history',name:'history',graph,created_ts:1,updated_ts:1};
      else if (path.endsWith('/runs')) body = Array.from({length:olderRank},(_,i)=>({snapshot_id:i===olderRank-1?'old':`new-${i}`,created_ts:100-i,workflow_id:'history',state_counts:{running:1},overall_state:'running',node_count:1,job_count:1}));
      else if (path.startsWith('/api/snapshots/')) { snapshots.push(path); body = {snapshot_id:'new-0',workflow_id:'history',graph,jobs:[],created_ts:100}; }
      else if (path.endsWith('/analyze')) body = {revision:'r',graph,diff:[],environment_params:{},staleness:{a:valid?null:{kind:'self_dirty',title:'配置已变'}},canvas_jobs:valid?[{...job,state}]:[]};
      else if (path === '/api/workflows' && route.request().method() === 'POST') body = {workflow_id:'history',name:'history',graph,created_ts:1,updated_ts:2};
      else if (['/api/jobs','/api/workflows'].includes(path)) body = [];
      await route.fulfill({json:body});
    });
    await page.goto('/w/history');
    const card = page.locator('[data-hololab-node="algorithm"]').first();
    await expect(card).toHaveAttribute('data-state','running');
    expect(snapshots.every(p=>p.endsWith('/new-0'))).toBe(true);
    // An old-snapshot completion is refreshed without a page reload.
    state = 'done';
    await expect(card).toHaveAttribute('data-state','done',{timeout:12000});
    // An invalidated result is removed, never retained by a preview fallback.
    valid = false;
    await expect(card).not.toHaveAttribute('data-state','done',{timeout:12000});
    await expect(card).toContainText('未运行');
  });
}
