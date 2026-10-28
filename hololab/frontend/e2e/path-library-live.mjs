// Opt-in isolated gateway with two real upgraded nodes; creates test resources/workflow.
import { chromium, expect } from '@playwright/test';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import assert from 'node:assert/strict';
const base=process.env.HOLOLAB_URL, video=process.env.LIBRARY_TEST_VIDEO;
if(!base||!video) throw Error('Set HOLOLAB_URL to an isolated gateway and LIBRARY_TEST_VIDEO to an MP4.');
const out=process.env.TEST_OUTPUT||'/tmp/path-library-live/evidence';await mkdir(out,{recursive:true});
const browser=await chromium.launch(), page=await browser.newPage({viewport:{width:1440,height:1000}});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
const api=async(path,data)=>{const r=await page.request.fetch(base+'/api'+path,{method:data===undefined?'GET':'POST',data});assert(r.ok(),`${path}: ${r.status()} ${await r.text()}`);return r.json();};
try {
 const nodes=(await api('/nodes')).filter(n=>n.online!==false);assert(nodes.length>=2);
 const a=nodes.find(n=>n.node_name==='Library test A')||nodes[0],b=nodes.find(n=>n.node_id!==a.node_id);
 const folder=await api('/library/folders',{name:'Path picker '+Date.now()});
 const bytes=await readFile(video),sha=createHash('sha256').update(bytes).digest('hex');
 async function upload(name,node){
  const s=await api('/library/uploads',{filename:name,size_bytes:bytes.length,sha256:sha,folder_id:folder.folder_id,target_node_ids:[node.node_id]});
  for(let start=0,i=0;start<bytes.length;start+=s.chunk_size,i++){
   const chunk=bytes.subarray(start,start+s.chunk_size),r=await page.request.put(base+`/api/library/uploads/${s.upload_id}/parts/${i}`,{data:chunk,headers:{'X-Chunk-Sha256':createHash('sha256').update(chunk).digest('hex')}});assert(r.ok(),await r.text());
  }
  await api(`/library/uploads/${s.upload_id}/complete`,{});
  await expect.poll(async()=>(await api('/library/resources/'+s.resource_id)).status,{timeout:120000}).toBe('ready');
  return api('/library/resources/'+s.resource_id);
 }
 const local=await upload('local-video.mp4',a),remote=await upload('remote-only.mp4',b);
 const workflow=await api('/workflows',{name:'Library path acceptance',graph:{nodes:[{id:'source',algorithm_name:'builtin/single-video-source',algorithm_version:'0.1.0',params:{},assigned_node_id:a.node_id,position:{x:100,y:100}}],edges:[]}});
 const config=(await api(`/nodes/${a.node_id}/config`)).config,expected=config.library_root+'/'+local.placements[0].relative_path;
 for(const theme of ['light','dark']){
  await page.goto(base+'/w/'+workflow.workflow_id);await page.evaluate(t=>{localStorage.setItem('hololab.theme',t);localStorage.setItem('hololab.locale','en');},theme);await page.reload();
  await page.locator('.react-flow__node[data-id="source"]').click();
  await page.getByRole('button',{name:'Browse paths for video_path'}).click();await page.screenshot({path:`${out}/entry-${theme}.png`});
  await page.getByRole('group',{name:'Path source'}).getByRole('button',{name:'Library',exact:true}).click();
  await page.getByRole('button',{name:`▸ ${folder.name} Folder`}).click();
  await expect(page.locator('.hl-path-library-entry').filter({hasText:'local-video.mp4'})).toBeVisible();await expect(page.getByText('remote-only.mp4')).toHaveCount(0);
  await page.screenshot({path:`${out}/library-${theme}.png`});
  await page.locator('.hl-path-library-entry').filter({hasText:'local-video.mp4'}).click();await page.getByRole('button',{name:'Select',exact:true}).click();
  await expect(page.getByRole('dialog')).not.toBeVisible();await expect(page.getByRole('combobox',{name:'video_path',exact:true})).toHaveValue(expected);
  await page.keyboard.press('Escape');await page.screenshot({path:`${out}/filled-${theme}.png`});
 }
 const pending=page.waitForResponse(r=>r.url().includes('/dispatch/source')&&r.request().method()==='POST');
 await page.locator('.react-flow__node[data-id="source"] .hl-node-run-control').click();const response=await pending;assert(response.ok(),await response.text());const dispatch=await response.json();
 await expect.poll(async()=>(await api('/jobs/'+dispatch.job_id)).state,{timeout:120000}).toBe('done');
 const job=await api('/jobs/'+dispatch.job_id);await page.screenshot({path:`${out}/run-success.png`});assert.deepEqual(errors,[]);
 await writeFile(`${out}/result.json`,JSON.stringify({base,workflow_id:workflow.workflow_id,folder,local,remote,expected,assigned_node:a.node_id,remote_node:b.node_id,dispatch,job,errors},null,2));
 console.log(JSON.stringify({workflow:workflow.workflow_id,path:expected,job:job.job_id,state:job.state,output:out}));
}finally{await browser.close();}
