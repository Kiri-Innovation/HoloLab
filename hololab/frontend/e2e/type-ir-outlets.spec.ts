import {test,expect} from '@playwright/test';
const wid='11111111-1111-1111-1111-111111111111';
for(const theme of ['light','dark']) {
 test(`IR draft inspector and rejected edge ${theme}`,async({page},info)=>{
  await page.setViewportSize({width:1500,height:1000});
  await page.addInitScript(t=>localStorage.setItem('hololab.theme',t),theme);
  await page.routeWebSocket('**/*',()=>{});
  const warnings:string[]=[];page.on('console',msg=>{if(msg.type()==='warning')warnings.push(msg.text());});
  const port={tags:['colmap-cams'],arrayed:true,dim_labels:['frame'],required:true};
  const pack={name:'merge-colmap',version:'0.4.0',manifest_hash:'fixture',node_ids:[],inputs:{cams:port,bad:{...port,tags:['image']}},outputs:{cams:port},params:{},arrayable:true};
  await page.route('**/api/**',async route=>{
   const path=new URL(route.request().url()).pathname;
   let body:unknown=[];
   if(path==='/api/pack-catalog')body=[pack];
   if(path===`/api/workflows/${wid}`) body={workflow_id:wid,name:'Type IR validation',created_ts:1,updated_ts:1,graph:{nodes:[0,1].map(i=>({id:`n${i}`,algorithm_name:pack.name,algorithm_version:pack.version,position:{x:i*430,y:100},params:{},assigned_node_id:null,arrayed_toggle:true})),edges:[{id:'e',source:'n0',sourceHandle:'cams',target:'n1',targetHandle:'cams'}]}};
   if(path==='/api/nodes/metrics/history')body={sample_interval_s:5,nodes:{}};
   if(path==='/api/artifacts/summary')body={total_bytes:0,exclusive_bytes:0,artifact_count:0};
   await route.fulfill({json:body});
  });
  await page.goto(`/w/${wid}`);
  const edge=page.locator('.hl-edge-chip');await expect(edge).toHaveCount(1);
  await page.locator('.react-flow__viewport').evaluate(el=>(el as HTMLElement).style.transform='translate(170px,180px) scale(1)');
  await edge.hover();await expect(page.getByRole('tooltip')).toBeVisible();
  await expect(page.locator('.hl-type-tooltip-after > span').first()).toHaveText('arrayed<arrayed[frame]<colmap-cams>>');
  await page.screenshot({path:info.outputPath(`${theme}-tooltip-and-edge.png`)});
  await edge.click();
  await expect(edge).toHaveAttribute('data-selected','');
  const chip=page.locator('span[title="arrayed<arrayed[frame]<colmap-cams>>"]');await expect(chip).toBeVisible();
  await page.mouse.move(0,0);await page.screenshot({path:info.outputPath(`${theme}-inspector.png`)});
  const source=page.locator('[data-id="n0"] .source[data-handleid="cams"]');
  const target=page.locator('[data-id="n1"] .target[data-handleid="bad"]');
  await source.hover();await page.mouse.down();await target.hover();await page.mouse.up();
  await expect.poll(()=>warnings.find(x=>x.startsWith('edge rejected:'))).toBe('edge rejected: arrayed<arrayed[frame]<colmap-cams>> → arrayed<arrayed[frame]<image>> incompatible');
  await expect(edge).toHaveCount(1);
  await info.attach('actual-browser-console',{body:warnings.join('\n'),contentType:'text/plain'});
  await page.screenshot({path:info.outputPath(`${theme}-rejected-connection.png`)});
 });
 test(`IR historical snapshot ${theme}`,async({page},info)=>{
  await page.setViewportSize({width:1200,height:800});
  await page.route('**/api/**',route=>route.fulfill({json:{}}));
  await page.goto(`/e2e/fixtures/type-ir-snapshot.html?theme=${theme}`);
  const edge=page.locator('.hl-edge-chip');await expect(edge).toHaveCount(1);
  await edge.hover();
  await expect(edge).toContainText('colmap-cams[historical-frame:?]');
  await expect(page.getByRole('tooltip')).toBeVisible();
  await expect(page.locator('.hl-type-tooltip-before > span').first()).toHaveText('arrayed[frame]<colmap-cams>');
  await expect(page.locator('.hl-type-tooltip-after')).toHaveCount(1);
  await page.screenshot({path:info.outputPath(`${theme}-snapshot.png`)});
  await edge.click();await expect(edge).toHaveAttribute('data-selected','');
 });
}
