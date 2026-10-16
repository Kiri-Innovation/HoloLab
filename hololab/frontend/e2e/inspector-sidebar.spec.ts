import { test, expect } from '@playwright/test';
import { mkdirSync } from 'node:fs';
const pack = { name:'inspector-demo',version:'1.0',manifest_hash:'demo',node_ids:['compute-a'],description:'Inspector layout verification',category:[],docs:'# Inspector demo\nParameter documentation remains available.',inputs:{},outputs:{},arrayable:true,params:{threshold:{type:'float',default:0.1,description:'Minimum confidence threshold used when filtering input samples.'},iterations:{type:'int',default:10000,description:'Maximum number of iterations.'},...Object.fromEntries(Array.from({length:12},(_,i)=>[`setting_${i+1}`,{type:'float',default:0.5}]))}};
const graph={nodes:[{id:'demo-node',algorithm_name:pack.name,algorithm_version:pack.version,position:{x:100,y:100},params:{},assigned_node_id:'compute-a',arrayed_toggle:false}],edges:[]};
test('sidebar selection, autosave, assignment, deletion and theme screenshots',async({page})=>{
 const before=process.env.INSPECTOR_BEFORE==='1';
 const output='test-results/inspector-sidebar'; mkdirSync(output,{recursive:true});
 const saved:any[]=[]; let currentGraph = structuredClone(graph); let runReads = 0;
 page.on('pageerror',e=>{throw e;});
 await page.routeWebSocket('**', () => {});
 await page.route('**/api/**',async route=>{
  const path=new URL(route.request().url()).pathname; let body:any=[];
  if(path.endsWith('/runs')) { runReads++; }
  else if(path==='/api/pack-catalog')body=[pack];
  else if(path==='/api/nodes')body=[{node_id:'compute-a',node_name:'Studio GPU',gpu:{count:1,name:'RTX 4090',total_vram_gb:24},packs:[pack],connected_ts:1,advertised_url:null,workspace_root:'/workspace',legacy_workspace_roots:[],flops_executor_id:null,packs_dir:'/packs',pack_dirs:['/packs']}];
  else if(path==='/api/nodes/metrics/history')body={sample_interval_s:3,nodes:{'compute-a':Array.from({length:20},(_,i)=>({ts:i+1,cpu_percent:20+i%7,mem_used_gb:12,mem_total_gb:64,gpus:[{index:0,name:'RTX 4090',util_percent:34+i%5,mem_used_mb:4096,mem_total_mb:24576}]}))}};
  else if(path==='/api/workflows/inspector-demo')body={workflow_id:'inspector-demo',name:'Inspector interaction review',graph:currentGraph,created_ts:1,updated_ts:1};
  else if(path==='/api/workflows'&&route.request().method()==='POST'){saved.push(route.request().postDataJSON()); currentGraph=saved.at(-1).graph; body={workflow_id:'inspector-demo',name:'Inspector interaction review',updated_ts:1+saved.length};}
  else if(path==='/api/jobs/demo-job/log')body={lines:[],truncated:false};
  else if(path==='/api/jobs/demo-job')body={job_id:'demo-job',state:'done',algorithm_name:pack.name,algorithm_version:pack.version};
  else if(path==='/api/jobs')body=[{job_id:'demo-job',algorithm_name:pack.name,algorithm_version:pack.version,state:'done',graph_node_id:'demo-node',workflow_id:'inspector-demo',created_ts:1,updated_ts:2}];
  await route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
 });
 await page.setViewportSize({width:1440,height:1000});
 await page.goto(`${before?'http://127.0.0.1:4174':process.env.INSPECTOR_URL||'http://127.0.0.1:5173'}/w/inspector-demo`);
 if(!before) {
   await expect(page.locator('.hl-inspector-sidebar').getByRole('tab',{name:'Runs',exact:true})).toHaveAttribute('aria-selected','true');
   await expect(page.getByRole('tab',{name:'Compute Nodes · Pulse',exact:true})).toHaveAttribute('aria-selected','true');
   await page.getByRole('tab',{name:'Jobs',exact:true}).click();
   await expect(page.locator('.hl-compute-dock').getByText('Recent jobs',{exact:true})).toBeVisible();
 }
 if(!before) {
   const jobsPanel=page.locator('#hl-bottom-panel-jobs');
   await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async(text:string)=>{(window as any).__copiedJob=text;}}}));
   await jobsPanel.getByRole('button',{name:'View job log',exact:true}).click();
   await expect(page.locator('[data-hl-job-log-modal]')).toBeVisible();
   await page.getByRole('button',{name:'Close',exact:true}).click();
   await jobsPanel.getByRole('button',{name:'复制引用给 AI',exact:true}).click();
   await expect.poll(()=>page.evaluate(()=>(window as any).__copiedJob)).toContain('demo-job');
   await jobsPanel.locator('.hl-job-row > :nth-child(2)').click();
   await expect(page.getByRole('tab',{name:'Inspector',exact:true})).toHaveAttribute('aria-selected','true');
   await page.getByRole('tab',{name:'Jobs',exact:true}).click();
 }
 const node=page.locator('[data-hololab-node="algorithm"]'); await expect(node).toBeVisible();
 for(const selected of [false,true]){if(selected)await node.click();for(const theme of ['light','dark']){await page.evaluate(t=>document.documentElement.dataset.theme=t,theme);await page.waitForTimeout(300);await page.screenshot({path:`${output}/${before?'before':'after'}-${selected?'selected':'unselected'}-${theme}.png`});}}
 if(before)return;
 const sidebar=page.locator('.hl-inspector-sidebar');await expect(sidebar.locator('.hl-node-inspector')).toBeVisible();expect((await sidebar.boundingBox())!.width).toBe(380);expect(await sidebar.evaluate(el=>el.scrollWidth<=el.clientWidth)).toBe(true);
 await expect(sidebar.getByText('Recent jobs',{exact:true})).not.toBeVisible();
 await expect(sidebar.getByRole('tab')).toHaveCount(2);
 // Window-frame strokes must not inherit generic button hover/press borders.
 for (const name of ['Inspector','Runs']) {
   const tab=sidebar.getByRole('tab',{name,exact:true});
   await page.mouse.move(500,100);
   const frame=await tab.evaluate(el=>({border:getComputedStyle(el).borderWidth,shadow:getComputedStyle(el).boxShadow,width:el.getBoundingClientRect().width}));
   await tab.hover();
   await page.mouse.down();
   expect(await tab.evaluate(el=>({border:getComputedStyle(el).borderWidth,shadow:getComputedStyle(el).boxShadow,width:el.getBoundingClientRect().width}))).toEqual(frame);
   await page.mouse.move(500,100); await page.mouse.up();
 }

 await page.getByRole('tab',{name:'Compute Nodes · Pulse',exact:true}).click();
 await expect(page.locator('.hl-compute-dock .hl-node-metrics')).toBeVisible();
 await sidebar.getByRole('textbox',{name:'setting_12',exact:true}).scrollIntoViewIfNeeded();
 await expect(sidebar.getByRole('textbox',{name:'setting_12',exact:true})).toBeVisible();
 await sidebar.getByRole('textbox',{name:'threshold',exact:true}).click();
 await sidebar.getByRole('textbox',{name:'threshold',exact:true}).press('ControlOrMeta+A');
 await sidebar.getByRole('textbox',{name:'threshold',exact:true}).pressSequentially('25');await expect.poll(()=>saved.at(-1)?.graph?.nodes[0]?.params?.threshold, {timeout:5000}).toBe(25);

 const inspectorTab=sidebar.getByRole('tab',{name:'Inspector',exact:true});
 const runsTab=sidebar.getByRole('tab',{name:'Runs',exact:true});
 for(const tab of ['Inspector','Runs']) {
   await sidebar.getByRole('tab',{name:tab,exact:true}).click();
   for(const theme of ['light','dark']) {
     await page.evaluate(t=>document.documentElement.dataset.theme=t,theme);
     await page.waitForTimeout(300);
     await page.screenshot({path:`${output}/dock-${tab}-${theme}.png`});
   }
 }
 await expect(sidebar.getByRole('textbox',{name:'threshold',exact:true})).not.toBeVisible();
 await runsTab.press('ArrowLeft');
 await expect(inspectorTab).toBeFocused();
 for(const tab of ['Compute Nodes · Pulse','Jobs']) {
   await page.getByRole('tab',{name:tab,exact:true}).click();
   for(const theme of ['light','dark']) {
     await page.evaluate(t=>document.documentElement.dataset.theme=t,theme); await page.waitForTimeout(300);
     await page.screenshot({path:`${output}/bottom-${tab==='Jobs'?'jobs':'pulse'}-${theme}.png`});
   }
 }
 await expect(sidebar.getByRole('textbox',{name:'threshold',exact:true})).toHaveValue('25');
 await page.getByRole('tab',{name:'Jobs',exact:true}).click();
 await expect(page.locator('.hl-compute-dock').getByText('Recent jobs',{exact:true})).toBeVisible();
 await expect(page.locator('.react-flow__node.selected')).toHaveCount(1);
 await page.waitForTimeout(1000);
 await expect(page.getByRole('tab',{name:'Jobs',exact:true})).toHaveAttribute('aria-selected','true');
 const pane=page.locator('.react-flow__pane');
 for(const theme of ['light','dark']) {
   await page.evaluate(t=>document.documentElement.dataset.theme=t,theme);
   await runsTab.click();
   // Clicking an already-selected node does not change selection or steal the tab.
   await node.click();
   await expect(runsTab).toHaveAttribute('aria-selected','true');
   await inspectorTab.click();
   await pane.click({position:{x:20,y:20}});
   await expect(runsTab).toHaveAttribute('aria-selected','true');
   await page.screenshot({path:`${output}/selection-cleared-${theme}.png`});
   // A manual choice with no selection also survives ordinary rerenders.
   await inspectorTab.click();
   await expect(sidebar.getByText('Select a node or connection to inspect it.')).toBeVisible();
   await page.waitForTimeout(350);
   await expect(inspectorTab).toHaveAttribute('aria-selected','true');
   await runsTab.click();
   await node.click();
   await expect(inspectorTab).toHaveAttribute('aria-selected','true');
   await expect(sidebar.getByRole('textbox',{name:'threshold',exact:true})).toHaveValue('25');
   await expect(page.getByRole('tab',{name:'Jobs',exact:true})).toHaveAttribute('aria-selected','true');
   await page.screenshot({path:`${output}/selection-active-${theme}.png`});
 }
 await expect(sidebar.getByRole('textbox',{name:'threshold',exact:true})).toHaveValue('25');
 await sidebar.getByLabel('Assigned node').selectOption('');await expect.poll(()=>saved.at(-1)?.graph?.nodes[0]?.assigned_node_id).toBe(null);
 await sidebar.getByLabel('Assigned node').selectOption('compute-a');await expect.poll(()=>saved.at(-1)?.graph?.nodes[0]?.assigned_node_id).toBe('compute-a');
 await sidebar.getByRole('button',{name:'About',exact:true}).click();await expect(page.getByRole('dialog')).toContainText('Parameter documentation');await page.keyboard.press('Escape');
 await sidebar.getByRole('button',{name:'Delete node',exact:true}).click();await expect.poll(()=>saved.at(-1)?.graph?.nodes.length).toBe(0); await expect(node).toHaveCount(0);await expect(runsTab).toHaveAttribute('aria-selected','true');await expect.poll(()=>saved.at(-1)?.graph?.nodes.length).toBe(0);
 await sidebar.getByRole('tab',{name:'Runs',exact:true}).click();
 const previousReads=runReads;
 await sidebar.getByRole('tabpanel',{name:'Runs',exact:true}).getByRole('button',{name:'刷新',exact:true}).click();
 await expect.poll(()=>runReads).toBeGreaterThan(previousReads);
 await page.setViewportSize({width:390,height:844});
 await page.getByRole('tab',{name:'Runs',exact:true}).click();
 await expect(page.locator('.hl-inspector-sidebar')).toHaveCount(0);
 await page.getByRole('tab',{name:'Jobs',exact:true}).click();
 await expect(page.getByText('Recent jobs',{exact:true})).toBeVisible();
 await page.screenshot({path:`${output}/dock-mobile.png`});
});
