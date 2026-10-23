// Real gateway, real UI gestures and real jobs; no request mocking.
import { chromium, expect } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
const base=process.env.BASE_URL || 'http://127.0.0.1:8828';
const workflow=process.env.CROP_WORKFLOW || '1f9ec9d4-e851-4fbc-828e-5940d3f22cfa';
const out=process.env.EVIDENCE_DIR || '/cloud/cloud-ssd1/Kiri4DGS/hololab/tmp/image-crop-viewer';
mkdirSync(out,{recursive:true});const browser=await chromium.launch();
try {
 const page=await browser.newPage({viewport:{width:1600,height:1000}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const get=async()=> (await page.request.get(`${base}/api/workflows/${workflow}`)).json();
 await page.goto(`${base}/w/${workflow}`);
 const node=page.locator('.react-flow__node[data-id="unequal"]');await node.waitFor();
 const expand=node.getByRole('button',{name:'展开预览',exact:true});if(await expand.count())await expand.click();
 const editor=node.getByRole('region',{name:'Image crop editor'});await expect(editor.locator('canvas')).toBeVisible();
 await node.click({position:{x:100,y:20}});await page.keyboard.press('f');
 const inspector=page.locator('.hl-node-inspector');
 const keys=['pad_left','pad_top','pad_right','pad_bottom'];
 for(const [i,key] of keys.entries()) {
  await inspector.getByLabel(key,{exact:true}).fill(String([32,17,53,29][i]));
  await expect(editor.getByLabel(`Crop ${key}`,{exact:true})).toHaveValue(String([32,17,53,29][i]));
 }
 await expect(editor.getByRole('button',{name:/Resize crop/})).toHaveCount(8);
 await page.screenshot({path:`${out}/01-visible.png`});const before=await get();
 const rect=await editor.getByLabel('Crop rectangle',{exact:true}).boundingBox();
 await page.mouse.move(rect.x+rect.width/2,rect.y+rect.height/2);await page.mouse.down();
 await page.mouse.move(rect.x+rect.width/2+12,rect.y+rect.height/2+8,{steps:8});
 await page.screenshot({path:`${out}/02-dragging.png`});await page.mouse.up();
 expect(Number(await editor.getByLabel('Crop pad_left').inputValue())).toBeGreaterThan(31);
 for(const side of ['n','ne','e','se','s','sw','w','nw']) {
  const r=await editor.getByRole('button',{name:`Resize crop ${side}`,exact:true}).boundingBox();
  await page.mouse.move(r.x+r.width/2,r.y+r.height/2);await page.mouse.down();
  await page.mouse.move(r.x+r.width/2+2,r.y+r.height/2+2,{steps:3});await page.mouse.up();
 }
 const params={};for(const key of keys){params[key]=Number(await editor.getByLabel(`Crop ${key}`).inputValue());await expect(inspector.getByLabel(key,{exact:true})).toHaveValue(String(params[key]));}
 await expect.poll(async()=> (await get()).graph.nodes.find(n=>n.id==='unequal').params.pad_left).toBe(params.pad_left);
 const after=await get();const run=g=>g.graph.nodes.find(n=>n.id==='unequal').latest_run;
 expect(run(after).job_id).toBe(run(before).job_id);
 for(const theme of ['light','dark']){await page.evaluate(t=>document.documentElement.dataset.theme=t,theme);await page.screenshot({path:`${out}/03-written-${theme}.png`});}
 await node.getByRole('button',{name:/^(重新)?运行$/}).click();
 await expect.poll(async()=>run(await get())?.job_id,{timeout:60000}).not.toBe(run(after).job_id);
 await expect.poll(async()=>run(await get())?.state,{timeout:60000}).toBe('done');
 const final=await get();const job=run(final),summaries=[];
 for(const [name,id] of [['source',final.graph.nodes.find(n=>n.id==='source').latest_run.output_handles.image.handle_id],['output',job.output_handles.image.handle_id]]) {
  const s=await(await page.request.get(`${base}/api/handles/${id}/summary`)).json();summaries.push(s);
  writeFileSync(`${out}/${name}.png`,await(await page.request.get(`${base}${s.proxy_url}`)).body());
 }
 const comparison=execFileSync('python',['-c',`from PIL import Image,ImageChops
import json,sys
p=json.loads(sys.argv[2]); src=Image.open(sys.argv[1]+'/source.png'); dst=Image.open(sys.argv[1]+'/output.png')
l,t,r,b=[p['pad_'+k] for k in ['left','top','right','bottom']]
expected=src.crop((l,t,src.width-r,src.height-b))
assert dst.size==expected.size,(dst.size,expected.size)
assert ImageChops.difference(dst,expected).getbbox() is None
print(json.dumps({'source':src.size,'output':dst.size,'pixel_exact':True}))`,out,JSON.stringify(params)]).toString();
 await node.locator('summary').filter({hasText:'上次运行产物'}).click();await page.screenshot({path:`${out}/04-job-output.png`});
 expect(errors).toEqual([]);writeFileSync(`${out}/evidence.json`,JSON.stringify({workflow,params,job,summaries,comparison:JSON.parse(comparison),errors},null,2));console.log(comparison);
}finally{await browser.close();}
