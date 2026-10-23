import {test,expect} from '@playwright/test';
import {writeFile} from 'node:fs/promises';

test('semantic event replay, SWR and React Profiler acceptance',async({page},testInfo)=>{
 await page.route('**/__stability',route=>route.fulfill({contentType:'text/html',body:`
 <div id="root"></div><script type="module">
 import React from '/node_modules/.vite/deps/react.js';
 import ReactDOM from '/node_modules/.vite/deps/react-dom_client.js';
 import {useExecutionAnalysis} from '/src/canvas/executionAnalysis.ts';
 import {applyStaleness} from '/src/canvas/applyStaleness.ts';
 import {analysisMetrics as counts,resetAnalysisMetrics as reset} from '/src/canvas/analysisMetrics.ts';
 window.counts=counts;window.reset=reset;window.profiler=[];window.renders={a:0,b:0};
 let hold=false,fail=false;window.release=()=>{};
 window.fetch=async(url,options)=>{
  const graph=JSON.parse(options.body).graph;
  const value=graph.nodes[0].params.x;
  const data={revision:String(value),graph,staleness:value===2?{a:{kind:'self_dirty',title:'changed'},b:{kind:'upstream_dirty',title:'old input'}}:{a:null,b:null},diff:[],environment_params:{}};
  if(hold){hold=false;return new Promise(resolve=>window.release=()=>resolve({ok:true,json:async()=>data}));}
  return {ok:!fail,json:async()=>fail?{detail:'test failure'}:data};
 };
 const node={id:'a',kind:'algorithm',algorithm_name:'foo',algorithm_version:'1.0.0',params:{x:1},position:{x:0,y:0}};
 const job={job_id:'parent',graph_node_id:'a',algorithm_name:'foo',algorithm_version:'1.0.0',params:{x:1},created_ts:1,state:'running'};
 const Card=React.memo(({node})=>{window.renders[node.id]++;return React.createElement('span',{'data-node':node.id},node.data.staleness?.title??'neutral');});
 function App(){
  const [workflow,setWorkflow]=React.useState('one');
  const [graph,setGraph]=React.useState({nodes:[node,{...node,id:'b'}],edges:[]});
  const [context,setContext]=React.useState({snapshotId:null,snapshotGraph:null,jobs:[],catalog:[],devices:[],reconnect:0});
  const [nodes,setNodes]=React.useState([{id:'a',data:{}},{id:'b',data:{}}]);
  const result=useExecutionAnalysis(workflow,graph,context);
  React.useEffect(()=>{setNodes(prev=>applyStaleness(prev,result.data?.staleness??{}));},[result.data]);
  const replay=(events)=>setContext(c=>{
   const jobs=new Map(c.jobs.map(j=>[j.job_id,j]));
   for(const event of events){counts.ws++;jobs.set(event.job_id,{...jobs.get(event.job_id),...event});}
   return {...c,jobs:[...jobs.values()]};
  });
  window.action=(name)=>{
   if(name==='start')setContext(c=>({...c,snapshotId:'s1',snapshotGraph:graph,jobs:[job]}));
   if(name==='shards')replay(Array.from({length:1000},(_,i)=>({...job,job_id:'shard'+i,parent_job_id:'parent',state:'done',progress:{current:i,total:1000}})));
   if(name==='progress')setContext(c=>{counts.ws++;return {...c,jobs:c.jobs.map(j=>({...j,updated_ts:Date.now(),progress:{current:9,total:100}}))};});
   if(name==='done'||name==='failed')setContext(c=>{counts.ws++;return {...c,jobs:c.jobs.map(j=>j.parent_job_id?j:{...j,state:name})};});
   if(name==='retry')setContext(c=>{counts.ws++;return {...c,jobs:[...c.jobs,{...job,job_id:'retry',created_ts:10}]};});
   if(name==='fanout')replay(Array.from({length:100},(_,i)=>['pending','assigned','running','done'].map(state=>({...job,job_id:'f'+i,parent_job_id:'parent',state}))).flat());
   if(name==='drag')setGraph(g=>({...g,nodes:g.nodes.map(n=>({...n,position:{x:100,y:200},preview_open:'out'}))}));
   if(name==='edit')setGraph(g=>({...g,nodes:g.nodes.map(n=>n.id==='a'?{...n,params:{x:2}}:n)}));
   if(name==='hold'){hold=true;setGraph(g=>({...g,nodes:g.nodes.map(n=>n.id==='a'?{...n,params:{x:3}}:n)}));}
   if(name==='fail'){fail=true;setContext(c=>({...c,reconnect:c.reconnect+1}));}
   if(name==='switch'){fail=false;setWorkflow('two');}
   if(name==='refresh')setContext(c=>({...c,reconnect:c.reconnect+1}));
  };
  return React.createElement(React.Fragment,{},
   React.createElement('output',{'data-state':result.status},result.message??result.data?.revision),
   React.createElement(React.Profiler,{id:'cards',onRender:(...args)=>window.profiler.push({id:args[0],phase:args[1],duration:args[2],renders:{...window.renders}})},nodes.map(n=>React.createElement(Card,{key:n.id,node:n}))));
 }
 ReactDOM.createRoot(document.getElementById('root')).render(React.createElement(App));
 </script>`}));
 await page.goto('/__stability');
 await expect(page.locator('output')).toHaveAttribute('data-state','unchecked');
 await expect(page.locator('output')).toHaveAttribute('data-state','ready');
 const records:any[]=[];
 const act=async(name:string)=>{await page.evaluate(name=>(window as any).action(name),name);};
 const measure=async(name:string)=>{
  await page.evaluate(()=>(window as any).reset());await act(name);await page.waitForTimeout(500);
  const counts=await page.evaluate(()=>({...((window as any).counts)}));records.push({name,...counts});return counts;
 };
 expect((await measure('start')).sent).toBe(1);
 expect((await measure('fanout')).sent).toBe(0);
 expect((await measure('done')).sent).toBe(1);
 expect((await measure('shards')).sent).toBe(0);
 expect((await measure('progress')).sent).toBe(0);
 expect((await measure('drag')).sent).toBe(0);
 const before=await page.evaluate(()=>({...((window as any).renders)}));
 expect((await measure('refresh')).replaced).toBe(0);
 const after=await page.evaluate(()=>({...((window as any).renders)}));
 expect(after).toEqual(before);
 expect((await measure('failed')).sent).toBe(1);
 expect((await measure('retry')).sent).toBe(1);
 expect((await measure('edit')).sent).toBe(1);
 await expect(page.locator('[data-node="a"]')).toHaveText('changed');
 await expect(page.locator('[data-node="b"]')).toHaveText('old input');
 await page.evaluate(()=>(window as any).reset());
 await act('hold');await page.waitForTimeout(300);
 await expect(page.locator('output')).toHaveText('检查中，保留上次结论');
 await act('edit');await expect(page.locator('output')).toHaveAttribute('data-state','ready');
 await page.evaluate(()=>(window as any).release());await page.waitForTimeout(300);
 await expect(page.locator('output')).toHaveText('2');
 const late=await page.evaluate(()=>({...((window as any).counts)}));
 expect(late.cancelled).toBe(1);expect(late.sent).toBe(2);records.push({name:'late-response',...late});
 expect((await measure('fail')).failed).toBe(1);
 await expect(page.locator('[data-node="a"]')).toHaveText('changed');
 await act('switch');await expect(page.locator('output')).toHaveAttribute('data-state','unchecked');
 await expect(page.locator('[data-node="a"]')).toHaveText('neutral');
 await expect(page.locator('output')).toHaveAttribute('data-state','ready');
 const profiler=await page.evaluate(()=>(window as any).profiler);
 await writeFile('/tmp/analysis-counts-and-profiler.json',JSON.stringify({records,profiler,equalResponseRenders:{before,after}},null,2));
 await testInfo.attach('analysis-counts-and-profiler.json',{body:JSON.stringify({records,profiler},null,2),contentType:'application/json'});
 console.log('ANALYSIS_ACCEPTANCE',JSON.stringify(records));
});

test('real App WebSocket replay leaves analysis quiet while progress updates',async({page},testInfo)=>{
 const pack={name:'test',version:'1.0.0',manifest_hash:'h',node_ids:['compute'],inputs:{},outputs:{},params:{},arrayable:true};
 const graph={nodes:[{id:'a',kind:'algorithm',algorithm_name:'test',algorithm_version:'1.0.0',params:{},assigned_node_id:'compute',arrayed_toggle:true,position:{x:100,y:100}}],edges:[]};
 const parent={job_id:'parent',workflow_id:'stability',snapshot_id:'s1',graph_node_id:'a',algorithm_name:'test',algorithm_version:'1.0.0',params:{},state:'running',expected_shards:100,created_ts:1,updated_ts:1};
 let socket:any;
 await page.routeWebSocket('**',ws=>{socket=ws;});
 await page.route('**/api/**',async route=>{
  const path=new URL(route.request().url()).pathname;let body:any=[];
  if(path==='/api/pack-catalog')body=[pack];
  else if(path==='/api/nodes')body=[{node_id:'compute',node_name:'Compute',gpu:{count:0},packs:[pack]}];
  else if(path==='/api/nodes/metrics/history')body={nodes:{}};
  else if(path==='/api/workflows/stability')body={workflow_id:'stability',name:'Stability',graph,created_ts:1,updated_ts:1};
  else if(path==='/api/workflows/stability/runs')body=[{snapshot_id:'s1',workflow_id:'stability',created_ts:1,node_count:1,job_count:1,state_counts:{running:1},state:'running'}];
  else if(path==='/api/snapshots/s1')body={snapshot_id:'s1',workflow_id:'stability',created_ts:1,graph,jobs:[parent]};
  else if(path.endsWith('/analyze'))body={revision:'r',graph,staleness:{a:null},diff:[],environment_params:{}};
  await route.fulfill({json:body});
 });
 await page.goto('/w/stability?debug=1');
 await expect(page.locator('[data-hololab-node="algorithm"]')).toHaveCount(1);
 await expect.poll(()=>page.evaluate(()=>(window as any).hololabAnalysis.counts.completed)).toBeGreaterThan(0);
 await page.waitForTimeout(500);
 const read=()=>page.evaluate(()=>({...((window as any).hololabAnalysis.counts)}));
 const reset=()=>page.evaluate(()=>(window as any).hololabAnalysis.reset());
 const send=(payload:any)=>socket.send(JSON.stringify({kind:'job_update',payload}));
 await reset();
 // Real WebSocket frames, delivered in ten separated bursts, not one React update.
 for(let batch=0;batch<10;batch++){
  for(let i=batch*10;i<(batch+1)*10;i++)for(const state of ['pending','assigned','running','done'])send({...parent,job_id:'shard'+i,parent_job_id:'parent',shard_element_id:String(i),expected_shards:null,state});
  await page.waitForTimeout(250);
 }
 await expect.poll(async()=>(await read()).ws).toBe(400);
 await page.waitForTimeout(400);
 const fanout=await read();expect(fanout.sent).toBe(0);
 await expect(page.locator('[data-hololab-node="algorithm"]')).toContainText('100/100');
 send({...parent,state:'done'});
 await expect.poll(async()=>(await read()).sent).toBe(1);
 await expect.poll(async()=>(await read()).completed).toBe(1);
 await reset();
 for(let batch=0;batch<10;batch++){
  for(let i=0;i<100;i++)send({...parent,job_id:'shard'+i,parent_job_id:'parent',state:'done',progress:{current:batch,total:10}});
  await page.waitForTimeout(250);
 }
 await expect.poll(async()=>(await read()).ws).toBe(1000);
 await page.waitForTimeout(400);const shards=await read();expect(shards.sent).toBe(0);
 await reset();send({...parent,state:'done',progress:{current:100,total:100}});
 await page.waitForTimeout(400);const progress=await read();expect(progress.sent).toBe(0);
 const card=page.locator('[data-hololab-node="algorithm"]');
 const box=await card.boundingBox();await page.mouse.move(box!.x+80,box!.y+16);await page.mouse.down();await page.mouse.move(box!.x+160,box!.y+76,{steps:12});await page.mouse.up();
 await page.waitForTimeout(500);const drag=await read();expect(drag.sent).toBe(0);
 await expect(page.locator('[data-status-tone="warning"]')).toHaveCount(0);
 const profile=await page.evaluate(()=>(window as any).hololabAnalysis.profile);
 expect(profile.length).toBeGreaterThan(0);
 const result={fanout,shards,progress,drag,profile};
 await writeFile('/tmp/analysis-app-ws-profiler.json',JSON.stringify(result,null,2));
 console.log('APP_WS_ACCEPTANCE',JSON.stringify({fanout,shards,progress,drag,profilerCommits:profile.length}));
 await testInfo.attach('app-ws-counts.json',{body:JSON.stringify(result,null,2),contentType:'application/json'});
 await page.screenshot({path:'/tmp/analysis-stability-app.png'});
});
