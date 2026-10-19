// Requires isolated gateway/node :19828 and source Vite :15184. No API mocks.
import { chromium } from 'playwright';
import { mkdirSync, writeFileSync, appendFileSync, readdirSync, readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
const base='http://127.0.0.1:19828',out='../../reports/array-params-mvp';mkdirSync(out,{recursive:true});
async function api(path,data){const r=await fetch(base+path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});assert(r.ok,await r.clone().text());return r.json();}
const catalog=await api('/api/pack-catalog');
for(const p of catalog.filter(p=>p.name!=='compose-video')){assert(Object.values(p.params).every(s=>s.type==='array'));appendFileSync(`/tmp/hololab-array-e2e/packs/${p.name}@0.1.0/manifest.yaml`,'\n# hot registration acceptance\n');}
for(let i=0;i<30;i++){const c=await api('/api/pack-catalog');if(catalog.filter(p=>p.name!=='compose-video').every(p=>c.find(q=>q.name===p.name)?.manifest_hash!==p.manifest_hash))break;if(i===29)throw Error('Hot registration timeout');await new Promise(r=>setTimeout(r,500));}
const compute=(await api('/api/nodes'))[0].node_id;
for(const [name,color] of [['a','red'],['b','blue']])execFileSync('ffmpeg',['-v','error','-y','-f','lavfi','-i',`color=${color}:s=160x120:r=8:d=1`,'-c:v','libx264',`/tmp/hololab-array-e2e/${name}.mp4`]);
const nodes=['video-array-assemble','float-array-source','vec3-array-source','compose-video'].map((name,i)=>({id:['videos','offsets','transforms','compose'][i],algorithm_name:name,algorithm_version:'0.1.0',assigned_node_id:compute,position:{x:80+i*230,y:150},params:{}}));
const edges=[['videos','videos','offsets','videos'],['videos','videos','transforms','videos'],['videos','videos','compose','videos'],['offsets','offsets','compose','offsets'],['transforms','transforms','compose','transforms']].map(([source,sourceHandle,target,targetHandle],i)=>({id:`e${i}`,source,sourceHandle,target,targetHandle}));
const {workflow_id:id}=await api('/api/workflows',{name:'Native array Inspector acceptance',graph:{nodes,edges}});
const browser=await chromium.launch();
try{
const page=await browser.newPage({viewport:{width:1500,height:1050}});
await page.route('**/api/**',async route=>{const u=new URL(route.request().url());const response=await route.fetch({url:base+u.pathname+u.search});await route.fulfill({response});});
await page.routeWebSocket('**/ws/**',ws=>ws.connectToServer(base.replace('http','ws')+new URL(ws.url()).pathname));
await page.goto(`http://127.0.0.1:15184/w/${id}`);
const panel=page.locator('.hl-node-inspector');
async function select(id){await page.locator(`.react-flow__node[data-id="${id}"]`).click();}
async function rows(name,values){for(let i=0;i<values.length;i++){await panel.getByRole('button',{name:`Add ${name} item`,exact:true}).click();await panel.getByLabel(`${name}[${i}]`,{exact:true}).fill(String(values[i]));await panel.getByLabel(`${name}[${i}]`,{exact:true}).press("Tab");}await panel.getByRole('button',{name:`Add ${name} item`,exact:true}).click();await panel.getByRole('button',{name:`Remove ${name}[${values.length}]`,exact:true}).click();}
async function shots(name){await panel.getByRole('heading',{name:'Parameters',exact:true}).click();for(const theme of ['light','dark']){await page.evaluate(t=>document.documentElement.dataset.theme=t,theme);await panel.screenshot({path:`${out}/${name}-${theme}.png`});}}
await select('videos');await rows('paths',['/tmp/hololab-array-e2e/a.mp4','/tmp/hololab-array-e2e/b.mp4']);await shots('path');
await select('offsets');await rows('items',[0,0.25]);await shots('float');
await select('transforms');
for (const [index, values] of [[0,[0,0,1]],[1,[0.4,0.2,0.5]]]) {
 await panel.getByRole('button',{name:'Add transform item',exact:true}).click();
 for (const [column,key] of ['x','y','scale'].entries()) await panel.getByLabel(`${key}[${index}]`,{exact:true}).fill(String(values[column]));
}
await panel.getByRole('button',{name:'Add transform item',exact:true}).click();
await panel.getByRole('button',{name:'Remove transform[2]',exact:true}).click();
await shots('vec3-components');
await page.setViewportSize({width:390,height:844});await page.getByRole('tab',{name:'Inspect',exact:true}).click();await panel.screenshot({path:`${out}/narrow.png`});assert(await panel.evaluate(el=>el.scrollWidth<=el.clientWidth));
await new Promise(r=>setTimeout(r,1500));
const native=(await api(`/api/workflows/${id}`)).graph;
assert.deepEqual(native.nodes.find(n=>n.id==='offsets').params.items,[0,0.25]);assert.deepEqual(native.nodes.find(n=>n.id==='transforms').params.scale,[1,0.5]);
writeFileSync(`${out}/native-graph.json`,JSON.stringify(native,null,2));
const legacy=structuredClone(native);
legacy.nodes.find(n=>n.id==='videos').params={paths_json:JSON.stringify(['/tmp/hololab-array-e2e/a.mp4','/tmp/hololab-array-e2e/b.mp4'])};
legacy.nodes.find(n=>n.id==='offsets').params={items:JSON.stringify([{offset:0},{offset:0.25}])};
legacy.nodes.find(n=>n.id==='transforms').params={items:JSON.stringify([{transform:[0,0,1]},{transform:[0.4,0.2,0.5]}])};
const old=await api('/api/workflows',{name:'Legacy array compatibility',graph:legacy});const before=await api(`/api/workflows/${old.workflow_id}`);
async function run(wid){const run=await api(`/api/workflows/${wid}/run`,{});for(let i=0;i<120;i++){const jobs=(await api('/api/jobs')).filter(j=>j.workflow_id===wid);if(jobs.some(j=>j.state==='failed'))throw Error(JSON.stringify(jobs));if(jobs.length===4&&jobs.every(j=>j.state==='done'))return {run,jobs};await new Promise(r=>setTimeout(r,500));}throw Error('Execution timeout');}
const nativeRun=await run(id),legacyRun=await run(old.workflow_id);const after=await api(`/api/workflows/${old.workflow_id}`);
assert.deepEqual(before.graph.nodes.map(n=>n.params),after.graph.nodes.map(n=>n.params));
for(const [name,data] of Object.entries({'live-results':{id,hotRegistration:true,nativeRun,legacyRun,legacyParamsUnchanged:true},'legacy-before':before.graph.nodes.map(n=>n.params),'legacy-after':after.graph.nodes.map(n=>n.params)}))writeFileSync(`${out}/${name}.json`,JSON.stringify(data,null,2));
const comparisons={};
for(const [node,port] of [['offsets','offsets'],['transforms','transforms'],['compose','video']]) {
 const hashes=[nativeRun,legacyRun].map(result=>{
  const job=result.jobs.find(j=>j.graph_node_id===node);
  const root=`/tmp/hololab-array-e2e/workspace/w/${job.workflow_id}/j/${job.job_id}/${port}`;
  return Object.fromEntries(readdirSync(root).sort().map(name=>[name,createHash('sha256').update(readFileSync(`${root}/${name}`)).digest('hex')]));
 });
 assert.deepEqual(hashes[0],hashes[1]);comparisons[node]=hashes[0];
}
writeFileSync(`${out}/output-comparison.json`,JSON.stringify(comparisons,null,2));
console.log('Live Inspector, hot registration, native/legacy compose and GET preservation passed.');
}finally{await browser.close();}
