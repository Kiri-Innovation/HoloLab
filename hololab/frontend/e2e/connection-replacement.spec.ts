import {test,expect} from '@playwright/test';
import fs from 'node:fs';
test.use({video:'on',viewport:{width:1600,height:1100}});
test('replace an input atomically, reject incompatible sources, keep output fan-out',async({page})=>{
  const pack=(name:string,tag:string,input=false)=>({name,version:'1',manifest_hash:'test',node_ids:[],params:{},inputs:input?{in:{tags:[tag]}}:{},outputs:input?{}:{out:{tags:[tag]}}});
  const catalog=[pack('image-source','image'),pack('mesh-source','mesh'),pack('image-target','image',true)];
  let graph:any={nodes:[['s1','image-source',0,0],['s2','image-source',0,210],['bad','mesh-source',0,420],['t1','image-target',420,70],['t2','image-target',420,350]].map(([id,name,x,y])=>({id,algorithm_name:name,algorithm_version:'1',params:{},position:{x,y}})),edges:[{id:'old',source:'s1',sourceHandle:'out',target:'t1',targetHandle:'in'}]};
  let version=1;const saved:any[]=[];
  await page.routeWebSocket('**',()=>{});
  await page.route('**/api/**',async route=>{
    const request=route.request(),path=new URL(request.url()).pathname;let body:any=[];
    if(path==='/api/pack-catalog')body=catalog;
    else if(path==='/api/nodes/metrics/history')body={nodes:{},sample_interval_s:3};
    else if(path==='/api/workflows/replace')body={workflow_id:'replace',name:'Input replacement verification',graph,created_ts:1,updated_ts:version};
    else if(path==='/api/workflows'&&request.method()==='POST'){
      const draft=request.postDataJSON();expect(draft.base_updated_ts).toBe(version);
      graph=draft.graph;saved.push(graph);body={workflow_id:'replace',updated_ts:++version};
    }
    await route.fulfill({json:body});
  });
  const connect=async(source:string,target:string)=>{
    const s=(await page.locator(`.react-flow__node[data-id="${source}"] .react-flow__handle.source`).boundingBox())!;
    const t=(await page.locator(`.react-flow__node[data-id="${target}"] .react-flow__handle.target`).boundingBox())!;
    await page.mouse.move(s.x+s.width/2,s.y+s.height/2);await page.mouse.down();await page.mouse.move(t.x+t.width/2,t.y+t.height/2,{steps:12});await page.mouse.up();
  };
  await page.goto('/w/replace');await expect(page.locator('.react-flow__node')).toHaveCount(5);
  await page.waitForTimeout(1000);
  fs.mkdirSync('/tmp/connection-replacement',{recursive:true});
  for(const theme of ['light','dark']){
    await page.evaluate(t=>document.documentElement.dataset.theme=t,theme);await page.waitForTimeout(250);
    if(theme==='dark'){await connect('s1','t1');await expect.poll(()=>graph.edges.find((e:any)=>e.target==='t1')?.source).toBe('s1');}
    await page.screenshot({path:`/tmp/connection-replacement/${theme}-before.png`});
    const count=saved.length;
    await connect('s2','t1');
    await expect.poll(()=>graph.edges.find((e:any)=>e.target==='t1')?.source).toBe('s2');
    expect(graph.edges.filter((e:any)=>e.target==='t1')).toHaveLength(1);
    expect(saved.slice(count).every(g=>g.edges.filter((e:any)=>e.target==='t1').length===1)).toBe(true);
    await page.screenshot({path:`/tmp/connection-replacement/${theme}-after.png`});
    const before=JSON.stringify(graph.edges);
    await connect('bad','t1');await page.waitForTimeout(1000);
    expect(JSON.stringify(graph.edges)).toBe(before);
    if(theme==='light'){await connect('s2','t2');await expect.poll(()=>graph.edges.filter((e:any)=>e.source==='s2').length).toBe(2);}
  }
  await page.reload();await expect(page.locator('.react-flow__edge')).toHaveCount(2);
  expect(graph.edges.every((e:any)=>e.source==='s2')).toBe(true);
});
