import { test, expect, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';

async function fixture(page: Page, grouped = false) {
  const pack:any = { name:'pin-demo',version:'1.0',manifest_hash:'pins',node_ids:['compute'],description:'Parameter pin regression fixture',
    docs:'# Parameter pins\nPin display is shared; values belong to individual nodes.', inputs:{input:{tags:['text'],arrayed:false}},outputs:{output:{tags:['text'],arrayed:false}},arrayable:true,
    params:{count:{type:'int',default:1,description:'Independent iteration count.'},mode:{type:'enum',default:1,values:[1,2,'2']},label:{type:'string',default:'hello'}} };
  if(grouped) Object.assign(pack,{name:'vec3-array-source',version:'0.1.0',params:{
    x:{type:'array',items:'float',default:[1]},y:{type:'array',items:'float',default:[2]},scale:{type:'array',items:'float',default:[1]}
  }});
  else Object.assign(pack.params,{
    enabled:{type:'bool',default:false},path:{type:'path',default:'/tmp/input.mp4'},
    items:{type:'array',items:'float',default:[1]},
    input_dims:{type:'list[str]',default:['camera']},output_dims:{type:'list[str]',default:['camera']}
  });
  const catalog = [pack, {...pack,version:'2.0'}];
  let graph:any={nodes:['a','b','c'].map((id,i)=>({id,algorithm_name:pack.name,algorithm_version:i===2?'2.0':pack.version,position:{x:40+i*250,y:100},params:{count:i+1},assigned_node_id:'compute',arrayed_toggle:i===1})),edges:[{id:'a-b',source:'a',sourceHandle:'output',target:'b',targetHandle:'input'}]};
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
  return {saved,initialGraph:graph};
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
    await page.waitForTimeout(250);
    await page.screenshot({path:`/tmp/parameter-pins/draft-${theme}.png`});
  }
  await page.reload();
  await expect(node('b').getByLabel('count',{exact:true})).toHaveValue('17');
  await node('a').locator('[data-hololab-node="algorithm"]').click({position:{x:70,y:20}});
  await inspector.getByRole('button',{name:'Unpin count on all pin-demo@1.0 nodes in this workflow',exact:true}).click();
  await expect(node('a').getByLabel('count',{exact:true})).toHaveCount(0);
  await expect(node('b').getByLabel('count',{exact:true})).toHaveCount(0);
  await expect(page.locator('.react-flow__edge-path').first()).toBeVisible();
  expect(await page.locator('.react-flow__edge-path').first().getAttribute('d')).not.toContain('NaN');
  await page.setViewportSize({width:390,height:800});
  await page.getByRole('tab',{name:'Inspect',exact:true}).click();
  await expect(inspector.getByLabel('mode',{exact:true})).toBeVisible();
  // The existing global toolbar can overflow; the Inspector controls must fit.
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
    await page.waitForTimeout(250);
    await page.screenshot({path:`/tmp/parameter-pins/snapshot-${theme}.png`});
  }
});

test('pinned array, path, bool and list controls share the Inspector editors',async({page})=>{
  const {saved}=await fixture(page);
  const node=page.locator('.react-flow__node[data-id="a"]');
  await node.locator('[data-hololab-node="algorithm"]').click({position:{x:70,y:20}});
  const inspector=page.locator('.hl-node-inspector');
  for(const name of ['enabled','path','items','output_dims'])
    await inspector.getByRole('button',{name:`Pin ${name} on all pin-demo@1.0 nodes in this workflow`,exact:true}).click();
  const pinned=node.getByRole('group',{name:'Pinned parameters',exact:true});
  await pinned.getByLabel('enabled',{exact:true}).check();
  await expect(inspector.getByLabel('enabled',{exact:true})).toBeChecked();
  await pinned.getByLabel('path',{exact:true}).fill('/tmp/edited.mp4');
  await expect(inspector.getByLabel('path',{exact:true})).toHaveValue('/tmp/edited.mp4');
  await pinned.getByLabel('items[0]',{exact:true}).fill('3.5');
  await pinned.getByRole('button',{name:'Add items item',exact:true}).click();
  await expect(inspector.getByLabel('items[0]',{exact:true})).toHaveValue('3.5');
  await expect(inspector.getByLabel('items[1]',{exact:true})).toHaveValue('0');
  const labels=await pinned.locator('input').evaluateAll(elements=>elements.map(el=>el.getAttribute('aria-label')));
  expect(labels.some(label=>label?.startsWith('output_dims'))).toBe(true);
  await expect.poll(()=>saved.at(-1)?.graph.nodes[0].params.items).toEqual([3.5,0]);
  expect(saved.at(-1).graph.nodes[0].params.enabled).toBe(true);
  expect(saved.at(-1).graph.nodes[0].params.path).toBe('/tmp/edited.mp4');
  // Typing F in a pinned text input must not frame/pan the canvas.
  const viewport=page.locator('.react-flow__viewport').first();
  const before=await viewport.getAttribute('style');
  await pinned.getByLabel('path',{exact:true}).press('f');
  await expect(viewport).toHaveAttribute('style',before!);
});

test('transform pin toggles x/y/scale as one row and grows node bounds',async({page})=>{
  const {saved}=await fixture(page,true);
  const node=page.locator('.react-flow__node[data-id="a"]');
  await node.locator('[data-hololab-node="algorithm"]').click({position:{x:70,y:20}});
  const inspector=page.locator('.hl-node-inspector');
  const before=(await node.boundingBox())!.height;
  await inspector.getByRole('button',{name:'Pin x / y / scale on all vec3-array-source@0.1.0 nodes in this workflow',exact:true}).click();
  const pinned=node.getByRole('group',{name:'Pinned parameters',exact:true});
  await expect(pinned.getByRole('group',{name:'transform array',exact:true})).toBeVisible();
  await pinned.getByLabel('x[0]',{exact:true}).fill('7');
  await expect(inspector.getByLabel('x[0]',{exact:true})).toHaveValue('7');
  await expect.poll(()=>saved.at(-1)?.graph.pinned_params).toEqual({'vec3-array-source@0.1.0':['x','y','scale']});
  await expect.poll(async()=>(await node.boundingBox())!.height).toBeGreaterThan(before);
  await pinned.getByRole('button',{name:'Add transform item',exact:true}).click();
  await expect(inspector.getByLabel('scale[1]',{exact:true})).toHaveValue('1');
  await inspector.getByRole('button',{name:'Unpin x / y / scale on all vec3-array-source@0.1.0 nodes in this workflow',exact:true}).click();
  await expect(page.getByRole('group',{name:'Pinned parameters',exact:true})).toHaveCount(0);
  await expect.poll(async()=>(await node.boundingBox())!.height).toBeCloseTo(before,0);
});

test('legacy enum pins preserve invalid values until explicitly edited',async({page})=>{
  const {initialGraph,saved}=await fixture(page);
  initialGraph.nodes[0].params.mode='legacy-value';
  initialGraph.pinned_params={'pin-demo@1.0':['mode','removed']};
  await page.reload();
  const node=page.locator('.react-flow__node[data-id="a"]');
  const pinned=node.getByRole('group',{name:'Pinned parameters',exact:true});
  await expect(pinned.getByLabel('mode',{exact:true})).toHaveValue('legacy-value');
  await expect(pinned).toContainText('Current value is not in the available options.');
  await expect(pinned).not.toContainText('removed');
  await pinned.getByLabel('mode',{exact:true}).fill('updated-legacy');
  await node.locator('[data-hololab-node="algorithm"]').click({position:{x:70,y:20}});
  await expect(page.locator('.hl-node-inspector').getByLabel('mode',{exact:true})).toHaveValue('updated-legacy');
  await expect.poll(()=>saved.at(-1)?.graph.nodes[0].params.mode).toBe('updated-legacy');
  expect(saved.at(-1).graph.pinned_params['pin-demo@1.0']).toEqual(['mode','removed']);
});

test('reloading an identical graph does not swallow the next edit',async({page})=>{
  await page.goto('/e2e/fixtures/parameter-pins.html?autosave');
  await page.getByRole('button',{name:'Reload unchanged workflow'}).click();
  await expect(page.getByLabel('Reloads')).toHaveText('1');
  await expect(page.getByLabel('Saves')).toHaveText('[]');
  await page.getByLabel('Value',{exact:true}).fill('first edit');
  await expect(page.getByLabel('Saves')).toHaveText('["first edit"]');
});
