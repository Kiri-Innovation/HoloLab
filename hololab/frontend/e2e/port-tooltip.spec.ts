import { test, expect } from '@playwright/test';
const wid = '11111111-1111-1111-1111-111111111111';
for (const theme of ['light', 'dark']) test(`port and edge tooltips ${theme}`, async ({ page }, info) => {
  await page.setViewportSize({width:1500,height:1000});
  await page.addInitScript(t=>localStorage.setItem('hololab.theme',t),theme);
  await page.routeWebSocket('**/*',()=>{});
  await page.route('**/api/**',async route=>{
    const path=new URL(route.request().url()).pathname;
    let body:unknown=[];
    const port={tags:['image','rgb'],arrayed:true,dim_labels:['frame'],required:true};
    if(path==='/api/pack-catalog') body=[{name:'demo',version:'1',manifest_hash:'x',node_ids:[],inputs:{input:port, spare:port},outputs:{out:port},params:{},arrayable:false}];
    if(path===`/api/workflows/${wid}`) body={workflow_id:wid,name:'Port tooltip',created_ts:1,updated_ts:1,graph:{nodes:[0,1].map(i=>({id:`n${i}`,algorithm_name:'demo',algorithm_version:'1',position:{x:i*430,y:100},params:{},assigned_node_id:null})),edges:[{id:'e',source:'n0',sourceHandle:'out',target:'n1',targetHandle:'input'}]}};
    if(path==='/api/nodes/metrics/history')body={sample_interval_s:5,nodes:{}};
    if(path==='/api/artifacts/summary')body={total_bytes:0,exclusive_bytes:0,artifact_count:0};
    await route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
  });
  await page.goto(`/w/${wid}`);
  const input=page.locator('[data-id="n1"] .react-flow__handle.target[data-handleid="input"]');
  const output=page.locator('[data-id="n0"] .react-flow__handle.source');
  const edge=page.locator('.hl-edge-chip');
  const tip=page.getByRole('tooltip');
  await expect(input).toBeVisible();
  const geometry=()=>page.locator('.react-flow__node, .react-flow__handle').evaluateAll(els=>els.map(el=>{const r=el.getBoundingClientRect();return [r.x,r.y,r.width,r.height]}));
  for(const zoom of [.5,1,1.5]){
    await page.mouse.move(0,0);
    await page.locator('.react-flow__viewport').evaluate((el,z)=>(el as HTMLElement).style.transform=`translate(180px,180px) scale(${z})`,zoom);
    const before=await geometry();
    for(const [name,anchor] of [['input',input],['output',output],['edge',edge]] as const){
      await anchor.hover();
      await expect(tip).toContainText('arrayed<frame> of image,rgb');
      await expect(tip).toHaveCSS('--tooltip-scale',String(zoom));
      await expect(anchor).not.toHaveAttribute('title');
      await page.screenshot({path:info.outputPath(`${theme}-${name}-${zoom}.png`)});
      await page.mouse.move(0,0);
    }
    expect(await geometry()).toEqual(before);
  }
  await edge.click();await expect(edge).toHaveAttribute('data-selected','');
  await output.hover();await expect(tip).toBeVisible();
  const spare=page.locator('[data-id="n1"] .react-flow__handle.target[data-handleid="spare"]');
  await page.mouse.down();await spare.hover();await page.waitForTimeout(450);
  await expect(tip).toHaveCount(0);
  await expect(page.locator('.react-flow__connection-path')).toHaveCount(1);
  await page.mouse.up();
  await expect(page.locator(".hl-edge-chip")).toHaveCount(2);
  await page.mouse.move(0,0);await input.hover();await expect(tip).toBeVisible();
});
