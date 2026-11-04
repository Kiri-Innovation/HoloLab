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

test('offers a graph-inferred replacement when a migrated historical pack has no declaration', async ({page}) => {
  const historical = 'community/images-array-source';
  const builtin = {name:'builtin/images-array-source',version:'0.1.1',manifest_hash:'builtin',node_ids:[],description:'Builtin replacement',inputs:{},outputs:{frames:{tags:['image'],arrayed:true,dim_labels:['frame']}},params:{}};
  const consumer = {name:'consumer',version:'1.0.0',manifest_hash:'consumer',node_ids:[],description:'Consumes frames',inputs:{frames:{tags:['image'],arrayed:true,dim_labels:['frame'],required:true}},outputs:{},params:{}};
  let graph:any = {nodes:[
    {id:'source',algorithm_name:historical,algorithm_version:'0.1.1',params:{frames_path:'/readonly'},position:{x:0,y:0}},
    {id:'sink',algorithm_name:'consumer',algorithm_version:'1.0.0',params:{},position:{x:420,y:0}},
  ],edges:[{id:'frames-wire',source:'source',sourceHandle:'frames',target:'sink',targetHandle:'frames'}]};
  let revision = 1;
  await page.routeWebSocket('**',()=>{});
  await page.route('**/api/**', async route => {
    const req = route.request(), path = new URL(req.url()).pathname;
    let body:any = [];
    if (path === '/api/pack-catalog') body = [builtin, consumer];
    else if (path === '/api/nodes/metrics/history') body = {nodes:{},sample_interval_s:3};
    else if (path === '/api/workflows/connection-inferred') body = {workflow_id:'connection-inferred',name:'Connection inferred',graph,created_ts:1,updated_ts:revision};
    else if (path === '/api/workflows/connection-inferred/runs') body = [];
    else if (path.endsWith('/analyze')) body = {revision:String(revision),graph:req.postDataJSON().graph,staleness:{},diff:[],environment_params:{},canvas_jobs:[]};
    else if (path === '/api/workflows' && req.method() === 'POST') { graph = req.postDataJSON().graph; body = {workflow_id:'connection-inferred',updated_ts:++revision}; }
    await route.fulfill({json:body});
  });
  await page.goto('/w/connection-inferred');
  const source = page.locator('.react-flow__node[data-id="source"]');
  await expect(source).toBeVisible();
  await source.click({button:'right'});
  await page.getByRole('menuitem',{name:'替换为算法…'}).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText('基于图上已使用的连线/内联输入推断（弱保证：未使用端口未验证）')).toBeVisible();
  await expect(dialog.getByRole('button',{name:'builtin/images-array-source @0.1.1'})).toBeVisible();
  await page.screenshot({path:'/tmp/algorithm-replacement/connection-inferred.png'});
  await dialog.getByRole('button',{name:'builtin/images-array-source @0.1.1'}).click();
  await expect.poll(() => graph.nodes[0].algorithm_name).toBe('builtin/images-array-source');
  expect(graph.nodes[0].id).toBe('source');
  expect(graph.edges).toEqual([{id:'frames-wire',source:'source',sourceHandle:'frames',target:'sink',targetHandle:'frames'}]);
});

test('offers a strict replacement for an arrayed legacy node with an invalid parameter', async ({page}) => {
  const io = {
    inputs: {video_dir: {tags:['video-source'], required:true}},
    outputs: {frame_sequence: {tags:['image'], arrayed:true, dim_labels:['frame'], storage:'dir'}},
  };
  const current = {name:'builtin/frame-extraction', version:'0.2.0', manifest_hash:'fx-020', node_ids:[], description:'Frame extraction', arrayable:true, ...io, params:{skip:{type:'int',default:1,min:1}}};
  const target = {...current, version:'0.2.1', manifest_hash:'fx-021', params:{...current.params, decode_threads:{type:'int',default:0,min:0}}};
  let graph:any = {nodes:[{id:'fx',algorithm_name:current.name,algorithm_version:current.version,params:{skip:0},arrayed_toggle:true,position:{x:0,y:0}}],edges:[]};
  await page.routeWebSocket('**',()=>{});
  await page.route('**/api/**', async route => {
    const req = route.request(), path = new URL(req.url()).pathname;
    let body:any = [];
    if (path === '/api/pack-catalog') body = [current, target];
    else if (path === '/api/nodes/metrics/history') body = {nodes:{},sample_interval_s:3};
    else if (path === '/api/workflows/fx-replace') body = {workflow_id:'fx-replace',name:'FX replacement',graph,created_ts:1,updated_ts:1};
    else if (path === '/api/workflows/fx-replace/runs') body = [];
    else if (path.endsWith('/analyze')) body = {revision:'1',graph:req.postDataJSON().graph,staleness:{fx:{kind:'invalid_params',title:"参数不合法：parameter 'skip': must be >= 1, got 0"}},diff:[],environment_params:{},canvas_jobs:[]};
    else if (path === '/api/workflows' && req.method() === 'POST') { graph = req.postDataJSON().graph; body = {workflow_id:'fx-replace',updated_ts:2}; }
    await route.fulfill({json:body});
  });
  await page.goto('/w/fx-replace');
  const node = page.locator('.react-flow__node[data-id="fx"]');
  await expect(node).toBeVisible();
  await node.dispatchEvent('contextmenu', {bubbles:true, clientX:180, clientY:180, button:2});
  await page.getByRole('menuitem',{name:'替换为算法…'}).dispatchEvent('click');
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText('依据本机 builtin/frame-extraction@0.2.0 的完整 I/O 声明')).toBeVisible();
  await expect(dialog.getByRole('button',{name:'builtin/frame-extraction @0.2.1'})).toBeVisible();
  await page.screenshot({path:'/tmp/algorithm-replacement/frame-extraction-021-candidate.png'});
  await dialog.getByRole('button',{name:'builtin/frame-extraction @0.2.1'}).click();
  await expect.poll(() => graph.nodes[0].algorithm_version).toBe('0.2.1');
  // The existing migration deliberately drops values rejected by the target
  // manifest and uses that manifest's default instead of persisting skip=0.
  expect(graph.nodes[0].params.skip).toBe(1);
});

test('treats an empty completed canvas-state selection as stale, not loading', async ({page}) => {
  const pack = {name:'demo',version:'1.0.0',manifest_hash:'demo',node_ids:[],description:'Demo',inputs:{},outputs:{out:{tags:['image']}},params:{}};
  const graph:any = {nodes:[{id:'source',algorithm_name:'demo',algorithm_version:'1.0.0',params:{},position:{x:0,y:0}}],edges:[]};
  const staleness = {source:{kind:'self_dirty',title:'有效配置与上次运行不同'}};
  await page.routeWebSocket('**',()=>{});
  await page.route('**/api/**', async route => {
    const req = route.request(), path = new URL(req.url()).pathname;
    let body:any = [];
    if (path === '/api/pack-catalog') body = [pack];
    else if (path === '/api/nodes/metrics/history') body = {nodes:{},sample_interval_s:3};
    else if (path === '/api/workflows/empty-canvas-state') body = {workflow_id:'empty-canvas-state',name:'Empty selection',graph,created_ts:1,updated_ts:1};
    else if (path === '/api/workflows/empty-canvas-state/canvas-state') body = {canvas_jobs:[],staleness};
    else if (path === '/api/workflows/empty-canvas-state/runs') body = [];
    else if (path.endsWith('/analyze')) body = {revision:'1',graph:req.postDataJSON().graph,staleness,diff:[],environment_params:{},canvas_jobs:[]};
    await route.fulfill({json:body});
  });
  await page.goto('/w/empty-canvas-state');
  const node = page.locator('.react-flow__node[data-id="source"]');
  await expect(node.locator('[data-status-tone="warning"]')).toBeVisible();
  await expect(node.getByText('读取中…',{exact:true})).toHaveCount(0);
});
