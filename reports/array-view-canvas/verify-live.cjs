const path=require('path');
const { chromium } = require(path.resolve(__dirname,'../../hololab/frontend/node_modules/playwright'));
const fs=require('fs');
const root=__dirname;
const wid=JSON.parse(fs.readFileSync(root+'/created-draft.json')).workflow_id;
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 const page=await browser.newPage({viewport:{width:1700,height:1000}});
 const failures=[],saves=[],dispatches=[];
 page.on('pageerror',e=>failures.push(e.message));
 page.on('request',r=>{if(/\/dispatch\/|\/run$/.test(new URL(r.url()).pathname)&&r.method()==='POST')dispatches.push(r.url());if(new URL(r.url()).pathname==='/api/workflows'&&r.method()==='POST')saves.push(r.postDataJSON());});
 await page.addInitScript(()=>localStorage.setItem('hololab.theme','light'));
 await page.goto('http://127.0.0.1:8828/w/'+wid);
 await page.locator('.react-flow__node[data-id="view-source"]').waitFor();
 await page.waitForTimeout(1500);
 const source=page.locator('[data-id="view-source"] .source[data-handleid="model"]');
 const drag=async(id,port)=>{
  const target=page.locator(`[data-id="${id}"] .target[data-handleid="${port}"]`);
  await source.hover();await page.mouse.down();await target.hover();
  await page.waitForTimeout(150);
  const feedback=page.locator('.hl-connection-tooltip');
  if(await feedback.getAttribute('data-valid')!=='true')throw new Error('rejected: '+await feedback.innerText());
  await page.mouse.up();
 };
 const before=await (await page.request.get('http://127.0.0.1:8828/api/workflows/'+wid)).json();
 if(before.name!=='Array view 接线验证（仅草稿，不运行）')throw new Error('refuse to edit another workflow');
 for(const [node,port] of [['view-index','arr'],['view-slice','in']]) {
  if(!before.graph.edges.some(e=>e.source==='view-source'&&e.sourceHandle==='model'&&e.target===node&&e.targetHandle===port)) await drag(node,port);
 }
 await page.waitForTimeout(2000);
 let readback=await (await page.request.get('http://127.0.0.1:8828/api/workflows/'+wid)).json();
 if(readback.graph.edges.length!==2)throw new Error('edges not persisted '+JSON.stringify(readback.graph.edges));
 await page.screenshot({path:root+'/light-connected.png'});
 await page.evaluate(()=>localStorage.setItem('hololab.theme','dark'));
 // init script must not overwrite the chosen theme on navigation.
 await page.close();
 const dark=await browser.newPage({viewport:{width:1700,height:1000}});
 await dark.addInitScript(()=>localStorage.setItem('hololab.theme','dark'));
 await dark.goto('http://127.0.0.1:8828/w/'+wid);await dark.locator('.react-flow__node[data-id="view-source"]').waitFor();await dark.waitForTimeout(1800);
 if(await dark.locator('.react-flow__edge').count()!==2)throw new Error('edges lost on reload');
 await dark.screenshot({path:root+'/dark-connected.png'});
 if(dispatches.length)throw new Error('unexpected dispatch request');
 const result={initial_edges:before.graph.edges.length,workflow_id:wid,url:'http://117.50.174.6:8828/w/'+wid,edges:readback.graph.edges,updated_ts:readback.updated_ts,save_count:saves.length,errors:failures,dispatches};
 fs.writeFileSync(root+'/browser-evidence.json',JSON.stringify(result,null,2));
 fs.writeFileSync(root+'/readback.json',JSON.stringify(readback,null,2));
 console.log(JSON.stringify(result,null,2));await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
