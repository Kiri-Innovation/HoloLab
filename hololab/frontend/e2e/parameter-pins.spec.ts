import { test, expect, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';

async function fixture(page: Page) {
  const pack = { name:'pin-demo',version:'1.0',manifest_hash:'pins',node_ids:['compute'],description:'Parameter pin regression fixture',
    docs:'# Parameter pins\nPin display is shared; values belong to individual nodes.', inputs:{},outputs:{},arrayable:true,
    params:{count:{type:'int',default:1,description:'Independent iteration count.'},mode:{type:'enum',default:1,values:[1,2,'2']},label:{type:'string',default:'hello'}} };
  const catalog = [pack, {...pack,version:'2.0'}];
  let graph:any={nodes:['a','b','c'].map((id,i)=>({id,algorithm_name:pack.name,algorithm_version:i===2?'2.0':'1.0',position:{x:40+i*250,y:100},params:{count:i+1},assigned_node_id:'compute',arrayed_toggle:i===1})),edges:[]};
  const saved:any[]=[];
  await page.routeWebSocket('**',()=>{});
  await page.route('**/api/**',async route=>{
    const request=route.request(); const path=new URL(request.url()).pathname; let body:any=[];
    if(path==='/api/pack-catalog')body=catalog;
    else if(path==='/api/nodes')body=[{node_id:'compute',node_name:'Test compute',packs:catalog,gpu:{count:0},connected_ts:1}];
    else if(path==='/api/nodes/metrics/history')body={sample_interval_s:3,nodes:{}};
    else if(path==='/api/workflows/pins')body={workflow_id:'pins',name:'Parameter pins — test fixture',graph,created_ts:1,updated_ts:1};
    else if(path==='/api/workflows'&&request.method()==='POST'){
      const data=request.postDataJSON(); saved.push(data); graph=data.graph; body={workflow_id:'pins',updated_ts:1};
    }
    await route.fulfill({json:body});
  });
  await page.setViewportSize({width:1600,height:1050});
  await page.goto('/w/pins');
  await expect(page.locator('[data-hololab-node="algorithm"]')).toHaveCount(3);
  return {saved};
}

test('workflow pins share display, not values, and survive save/reload',async({page})=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  const {saved}=await fixture(page);
  const node=(id:string)=>page.locator(`.react-flow__node[data-id="${id}"]`);
  await node('a').locator('[data-hololab-node="algorithm"]').click({position:{x:70,y:20}});
  const inspector=page.locator('.hl-node-inspector');
  await inspector.getByRole('button',{name:'Pin count on all pin-demo@1.0 nodes in this workflow',exact:true}).click();
  await expect(node('a').getByLabel('count',{exact:true})).toHaveValue('1');
  await expect(node('b').getByLabel('count',{exact:true})).toHaveValue('2');
  await expect(node('c').getByRole('group',{name:'Pinned parameters'})).toHaveCount(0);
  await node('b').getByLabel('count',{exact:true}).fill('17');
  await expect(inspector.getByLabel('count',{exact:true})).toHaveValue('1');
  await inspector.getByLabel('count',{exact:true}).fill('9');
  await expect(node('a').getByLabel('count',{exact:true})).toHaveValue('9');
  await expect(node('b').getByLabel('count',{exact:true})).toHaveValue('17');
  await inspector.getByRole('button',{name:'Pin mode on all pin-demo@1.0 nodes in this workflow',exact:true}).click();
  await node('a').getByLabel('mode',{exact:true}).click();
  await page.getByRole('option',{name:'2',exact:true}).first().click();
  await expect.poll(()=>saved.at(-1)?.graph.nodes.find((n:any)=>n.id==='a').params.mode).toBe(2);
  expect(saved.at(-1).graph.pinned_params).toEqual({'pin-demo@1.0':['count','mode']});
  const sidebar=page.locator('.hl-inspector-sidebar');
  await sidebar.getByRole('tab',{name:'Runs',exact:true}).click();
  await sidebar.getByRole('tab',{name:'Inspector',exact:true}).click();
  await expect(inspector.getByLabel('count',{exact:true})).toHaveValue('9');
  mkdirSync('/tmp/parameter-pins',{recursive:true});
  for(const theme of ['light','dark']){
    await page.evaluate(t=>document.documentElement.dataset.theme=t,theme);
    await page.screenshot({path:`/tmp/parameter-pins/draft-${theme}.png`});
  }
  await page.reload();
  await expect(node('b').getByLabel('count',{exact:true})).toHaveValue('17');
  await node('a').locator('[data-hololab-node="algorithm"]').click({position:{x:70,y:20}});
  await inspector.getByRole('button',{name:'Unpin count on all pin-demo@1.0 nodes in this workflow',exact:true}).click();
  await expect(node('a').getByLabel('count',{exact:true})).toHaveCount(0);
  await expect(node('b').getByLabel('count',{exact:true})).toHaveCount(0);
  await page.setViewportSize({width:390,height:800});
  await page.getByRole('tab',{name:'Inspect',exact:true}).click();
  await expect(inspector.getByLabel('mode',{exact:true})).toBeVisible();
  // The pre-existing global toolbar overflows at 390px; pin controls must not.
  expect(await inspector.evaluate(el=>el.scrollWidth<=el.clientWidth)).toBe(true);
  await inspector.getByLabel('mode',{exact:true}).scrollIntoViewIfNeeded();
  await page.screenshot({path:'/tmp/parameter-pins/narrow.png'});
  expect(errors).toEqual([]);
});

test('snapshot uses frozen pins, hides missing fields, and cannot edit',async({page})=>{
  await page.route('**/api/**',r=>r.fulfill({json:[]}));
  await page.goto('/e2e/fixtures/parameter-pins.html');
  const pinned=page.getByRole('group',{name:'Pinned parameters'});
  await expect(pinned.getByLabel('count (read only)')).toHaveText('42');
  await expect(pinned.locator('input,button')).toHaveCount(0);
  await expect(pinned).not.toContainText('label');
  await expect(pinned).not.toContainText('removed');
  mkdirSync('/tmp/parameter-pins',{recursive:true});
  for(const theme of ['light','dark']){
    await page.evaluate(t=>document.documentElement.dataset.theme=t,theme);
    await page.screenshot({path:`/tmp/parameter-pins/snapshot-${theme}.png`});
  }
});
