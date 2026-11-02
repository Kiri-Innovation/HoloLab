import {test,expect} from '@playwright/test';
import fs from 'node:fs';
test.use({video:'on',viewport:{width:1500,height:1000}});
for (const delayed of [false, true]) test(`replace algorithm updates analysis, autosave and reload (delayed=${delayed})`,async({page})=>{
  let releaseAnalysis!: () => void;
  const heldAnalysis = new Promise<void>(resolve => { releaseAnalysis = resolve; });
  const param=(type:string,value:unknown,pinned=false)=>({type,default:value,optional:false,description:'Verification parameter',pinned});
  const base={name:'demo',version:'1.0.0',manifest_hash:'test',node_ids:[],description:'Algorithm replacement verification',inputs:{in:{tags:['image']}},outputs:{out:{tags:['image']}},params:{count:param('int',2),remove:param('string','old')}};
  const next={...base,version:'2.0.0',params:{count:param('int',3,true),enabled:param('bool',true,true)}};
  const bad={...base,name:'incompatible',inputs:{in:{tags:['mesh']}}};
  const unique={...base,name:'unique',inputs:{in:{tags:['unique']}}};
  let graph:any={pinned_params:{'demo@1.0.0':['remove']},nodes:[{id:'source',algorithm_name:'demo',algorithm_version:'1.0.0',params:{count:17,remove:'drop'},position:{x:0,y:0}},{id:'target',algorithm_name:'unique',algorithm_version:'1.0.0',params:{},position:{x:400,y:0}}],edges:[{id:'wire',source:'source',sourceHandle:'out',target:'target',targetHandle:'in'}]};
  const initial = structuredClone(graph);
  const analyzed:string[] = [];
  const job={job_id:'done',snapshot_id:'s1',workflow_id:'replace-algorithm',graph_node_id:'source',algorithm_name:'demo',algorithm_version:'1.0.0',params:{count:17,remove:'drop'},state:'done',created_ts:1};
  let version=1;
  await page.routeWebSocket('**',()=>{});
  await page.route('**/api/**',async route=>{
    const req=route.request(),path=new URL(req.url()).pathname;let body:any=[];
    if(path==='/api/pack-catalog')body=[base,next,bad,unique];
    else if(path==='/api/nodes/metrics/history')body={nodes:{},sample_interval_s:3};
    else if(path==='/api/workflows/replace-algorithm')body={workflow_id:'replace-algorithm',name:'Algorithm replacement',graph,created_ts:1,updated_ts:version};
    else if(path==='/api/workflows/replace-algorithm/runs')body=[{snapshot_id:'s1',workflow_id:'replace-algorithm',created_ts:1,node_count:2,job_count:1,state_counts:{done:1},state:'done'}];
    else if(path==='/api/snapshots/s1')body={snapshot_id:'s1',workflow_id:'replace-algorithm',created_ts:1,graph:initial,jobs:[job]};
    else if(path.endsWith('/analyze')) {
      const current=req.postDataJSON().graph, selected=current.nodes[0].algorithm_version;
      analyzed.push(selected);
      const dirty=selected!=='1.0.0';
      if (dirty && delayed) await heldAnalysis;
      body={revision:selected,graph:current,staleness:{source:dirty?{kind:'self_dirty',title:'有效配置与上次运行不同'}:null},diff:dirty?[{description:'版本已变化'}]:[],environment_params:{},canvas_jobs:dirty?[]:[job]};
    }
    else if(path==='/api/workflows'&&req.method()==='POST') {const draft=req.postDataJSON();expect(draft.base_updated_ts).toBe(version);graph=draft.graph;body={workflow_id:'replace-algorithm',updated_ts:++version};}
    await route.fulfill({json:body});
  });
  fs.mkdirSync('/tmp/algorithm-replacement',{recursive:true});
  await page.goto('/w/replace-algorithm');await expect(page.locator('.react-flow__node')).toHaveCount(2);
  const source=page.locator('.react-flow__node[data-id="source"]');
  await expect(source.locator('[data-status-tone="success"]')).toBeVisible();
  await source.click({button:'right'});await page.getByRole('menuitem',{name:'替换为算法…'}).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await expect(page.getByRole('dialog').getByRole('button',{name:/当前/})).toBeDisabled();
  await expect(page.getByRole('dialog').getByText('incompatible')).toHaveCount(0);
  for(const theme of ['light','dark']) {await page.evaluate(t=>document.documentElement.dataset.theme=t,theme);await page.screenshot({path:`/tmp/algorithm-replacement/${theme}-candidates.png`});}
  await page.setViewportSize({width:390,height:844});
  await expect.poll(()=>page.getByRole('dialog').evaluate(el=>el.scrollWidth<=el.clientWidth)).toBe(true);
  await page.screenshot({path:'/tmp/algorithm-replacement/narrow.png'});
  await page.setViewportSize({width:1500,height:1000});
  await page.getByRole('textbox',{name:'搜索算法'}).fill('2.0');
  await page.getByRole('button',{name:'demo @2.0.0'}).click();
  await expect.poll(()=>analyzed.at(-1)).toBe('2.0.0');
  if (delayed) await expect(source.locator('[data-status-tone="warning"]')).toBeVisible();
  else await expect(source).toContainText('未运行');
  await page.getByRole('tab',{name:'Runs',exact:true}).click();
  await expect(page.getByText('运行将创建新快照',{exact:false})).toBeVisible();
  releaseAnalysis();
  await expect(source).toContainText('未运行');
  await expect.poll(()=>graph.nodes[0].algorithm_version).toBe('2.0.0');
  expect(graph.nodes[0].id).toBe('source');expect(graph.nodes[0].position).toEqual({x:0,y:0});
  expect(graph.nodes[0].params).toEqual({count:17,enabled:true});expect(graph.edges).toHaveLength(1);
  expect(graph.pinned_params).toEqual({'demo@1.0.0':['remove']});
  for(const theme of ['light','dark']) {await page.evaluate(t=>document.documentElement.dataset.theme=t,theme);await page.screenshot({path:`/tmp/algorithm-replacement/${theme}-replaced.png`});}
  await page.reload();await expect(source).toContainText('v2.0.0');await expect(source).toContainText('enabled');
  await page.locator('.react-flow__node[data-id="target"]').click({button:'right'});
  await expect(page.getByRole('menuitem',{name:'替换为算法…'})).toBeDisabled();
  await page.keyboard.press('Escape');
  await source.click({button:'right'});await page.getByRole('menuitem',{name:'创建副本'}).click();await expect(page.locator('.react-flow__node')).toHaveCount(3);
});
