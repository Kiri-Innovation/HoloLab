import {test, expect} from '@playwright/test';
import fs from 'node:fs';

test.use({video:'on', viewport:{width:1440,height:1000}});
test('graph edits persist through delayed and immediate reloads on the gateway', async({page,request})=>{
  test.skip(!process.env.DRAFT_LIVE, 'Explicit opt-in: creates and removes its own empty draft, never runs jobs');
  const api='http://127.0.0.1:8828';
  const catalog=await (await request.get(api+'/api/pack-catalog')).json();
  const pack=catalog.find((p:any)=>p.name==='builtin/single-video-source');
  expect(pack).toBeTruthy();
  const created=await request.post(api+'/api/workflows',{data:{name:'Autosave verification (temporary)',graph:{nodes:[{id:'source',algorithm_name:pack.name,algorithm_version:pack.version,position:{x:0,y:0},params:{}}],edges:[]}}});
  expect(created.ok()).toBe(true);
  const {workflow_id:id}=await created.json();
  const posts:any[]=[];
  const beacons:any[]=[];
  page.on('console', message=>{if(message.text().startsWith('AUTOSAVE_BEACON '))beacons.push(JSON.parse(message.text().slice(16)));});
  await page.addInitScript(()=>{
    const send=navigator.sendBeacon.bind(navigator);
    navigator.sendBeacon=(url,data)=>{const queued=send(url,data);console.info('AUTOSAVE_BEACON '+JSON.stringify({url:String(url),queued,bytes:data instanceof Blob?data.size:null}));return queued;};
  });
  page.on('response',async response=>{if(response.request().method()==='POST'&&new URL(response.url()).pathname==='/api/workflows')posts.push({status:response.status(),transport:response.request().resourceType(),body:response.request().postDataJSON()});});
  const read=async()=>await(await request.get(api+'/api/workflows/'+id)).json();
  fs.mkdirSync('/tmp/draft-persistence',{recursive:true});
  try {
    await page.goto('/w/'+id);
    await expect(page.locator('.react-flow__node[data-id="source"]')).toBeVisible();
    await page.waitForTimeout(1000);
    const canvas=page.locator('.react-flow');
    const b=(await canvas.boundingBox())!;
    await canvas.evaluate((el,p)=>{const dataTransfer=new DataTransfer();dataTransfer.setData('application/hololab-view','artifact-preview');el.dispatchEvent(new DragEvent('drop',{bubbles:true,clientX:p.x,clientY:p.y,dataTransfer}));},{x:b.x+b.width*.8,y:b.y+b.height*.65});
    const view=page.locator('.react-flow__node').filter({has:page.locator('[data-hl-view-node]')});
    await expect(view).toHaveCount(1);
    const source=page.locator('.react-flow__node[data-id="source"] .react-flow__handle.source').first();
    const target=view.locator('.react-flow__handle.target').first();
    const s=(await source.boundingBox())!,t=(await target.boundingBox())!;
    await page.mouse.move(s.x+s.width/2,s.y+s.height/2);await page.mouse.down();await page.mouse.move(t.x+t.width/2,t.y+t.height/2,{steps:8});await page.mouse.up();
    await expect(page.locator('.react-flow__edge')).toHaveCount(1);
    await page.waitForTimeout(3000);
    const saved=await read();
    fs.writeFileSync('/tmp/draft-persistence/'+(process.env.DRAFT_BEFORE?'before':'after')+'.json',JSON.stringify({posts,graph:saved.graph},null,2));
    if(process.env.DRAFT_BEFORE){expect(posts).toHaveLength(0);expect(saved.graph.nodes).toHaveLength(1);await page.reload();await expect(page.locator('.react-flow__node')).toHaveCount(1);return;}
    expect(saved.graph.nodes).toHaveLength(2);expect(saved.graph.edges).toHaveLength(1);
    await page.reload();await expect(page.locator('.react-flow__node')).toHaveCount(2);await expect(page.locator('.react-flow__edge')).toHaveCount(1);
    await page.waitForTimeout(1000);
    // Add another node, then connect and reload before the trailing debounce.
    await canvas.evaluate((el,p)=>{const dataTransfer=new DataTransfer();dataTransfer.setData('application/hololab-view','artifact-preview');el.dispatchEvent(new DragEvent('drop',{bubbles:true,clientX:p.x,clientY:p.y,dataTransfer}));},{x:b.x+b.width*.8,y:b.y+b.height*.35});
    await expect(view).toHaveCount(2);
    await expect(page.locator('[data-hl-save-status="saved"]')).toBeVisible();
    const s2=(await source.boundingBox())!, t2=(await view.last().locator('.react-flow__handle.target').boundingBox())!;
    await page.mouse.move(s2.x+s2.width/2,s2.y+s2.height/2);await page.mouse.down();await page.mouse.move(t2.x+t2.width/2,t2.y+t2.height/2,{steps:4});await page.mouse.up();
    await expect(page.locator('.react-flow__edge')).toHaveCount(2);
    await page.reload();
    await expect.poll(async()=>(await read()).graph.edges.length).toBe(2);
    await expect(page.locator('.react-flow__edge')).toHaveCount(2);
    await page.waitForTimeout(1000);
    const freshBounds=(await canvas.boundingBox())!;
    await canvas.evaluate((el,p)=>{const dataTransfer=new DataTransfer();dataTransfer.setData('application/hololab-view','artifact-preview');el.dispatchEvent(new DragEvent('drop',{bubbles:true,clientX:p.x,clientY:p.y,dataTransfer}));},{x:freshBounds.x+freshBounds.width*.6,y:freshBounds.y+freshBounds.height*.85});
    await expect(view).toHaveCount(3);
    await page.reload();
    await expect(page.locator('.react-flow__node')).toHaveCount(4);
    expect((await read()).graph.nodes).toHaveLength(4);
    // Save errors stay visible and the existing Retry action uses the same pipe.
    await page.waitForTimeout(1000);
    await page.route('**/api/workflows',route=>route.request().method()==='POST'?route.fulfill({status:503,json:{detail:'Injected save failure'}}):route.continue());
    await page.getByPlaceholder('workflow name').fill('Retry verification');
    await expect(page.locator('[data-hl-save-status="error"]')).toBeVisible();
    await page.waitForTimeout(1500);
    await expect(page.locator('[data-hl-save-status="error"]')).toBeVisible();
    await page.screenshot({path:'/tmp/draft-persistence/save-error.png'});
    await page.unroute('**/api/workflows');
    await page.getByRole('button',{name:'重试保存',exact:true}).click();
    await expect(page.locator('[data-hl-save-status="saved"]')).toBeVisible();
    expect((await read()).name).toBe('Retry verification');
    for(const theme of ['light','dark']){await page.evaluate(t=>document.documentElement.dataset.theme=t,theme);await page.waitForTimeout(300);await page.screenshot({path:'/tmp/draft-persistence/'+theme+'.png'});}
    expect(beacons.filter(b=>b.queued).length).toBeGreaterThanOrEqual(2);
    expect((await(await request.get(api+'/api/workflows/'+id+'/runs')).json())).toHaveLength(0);
    fs.writeFileSync('/tmp/draft-persistence/after.json',JSON.stringify({posts,beacons,workflow:await read()},null,2));
  } finally {await page.goto('about:blank');await request.delete(api+'/api/workflows/'+id);}
});
