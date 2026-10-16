// Run against a local Vite server: HOLOLAB_URL=http://127.0.0.1:5189 node src/library/gallery.acceptance.mjs
import { chromium, expect } from '@playwright/test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
const origin = process.env.HOLOLAB_URL || 'http://127.0.0.1:5189';
const browser = await chromium.launch();
const page = await browser.newPage({viewport:{width:1440,height:1000}});
const errors=[];page.on('pageerror',error=>errors.push(error.message));
const CHUNK=8*1024**2, buffer=Buffer.alloc(CHUNK*2+17,42);
const sha=data=>createHash('sha256').update(data).digest('hex');
let session=null, fail=true, complete=false, deleting=false;
const parts=new Map(), calls=[];
const folders=[{folder_id:'root',parent_id:null,name:''},{folder_id:'projects',parent_id:'root',name:'Projects'},{folder_id:'footage',parent_id:'projects',name:'Footage'}];
const resource={resource_id:'r1',folder_id:'projects',name:'resume.mp4',resource_type:'generic',size_bytes:buffer.length,ready_count:1,target_count:1,placements:[{node_id:'online',node_name:'Studio GPU',online:1,desired_state:'present',observed_state:'ready'}],transfers:[]};
await page.route('**/api/**',async route=>{
 const req=route.request(),url=new URL(req.url());let body;
 if(url.pathname==='/api/nodes') body=[{node_id:'online',node_name:'Studio GPU',online:true},{node_id:'offline',node_name:'Offline node',online:false}];
 else if(url.pathname==='/api/library/folders' && req.method()==='POST') {const data=req.postDataJSON();assert.equal(data.parent_id,'projects');folders.push({folder_id:'new',...data});body=folders.at(-1);}
 else if(url.pathname==='/api/library/folders') body={folders};
 else if(url.pathname==='/api/library/resources') body={total:session?1:0,resources:session&&url.searchParams.get('folder_id')==='projects'?[{...resource,status:deleting?'deleting':complete?'ready':'uploading',remaining_nodes:1}]:[]};
 else if(url.pathname==='/api/library/resources/r1' && req.method()==='DELETE'){deleting=true;body={status:'deleting'};}
 else if(url.pathname==='/api/library/resources/r1') body={...resource,status:complete?'ready':'uploading'};
 else if(url.pathname==='/api/library/uploads' && req.method()==='POST') {
   const data=req.postDataJSON();assert.equal(data.folder_id,'projects');assert.deepEqual(data.target_node_ids,['online']);assert.equal(data.sha256,sha(buffer));
   session={upload_id:'u1',resource_id:'r1',version:1,state:'uploading',expected_size:buffer.length,expected_sha256:sha(buffer),chunk_size:CHUNK};body=session;
 } else if(url.pathname==='/api/library/uploads') body={sessions:session?[session]:[]};
 else if(url.pathname.includes('/parts/')) {
   const index=Number(url.pathname.split('/').at(-1));calls.push(index);const data=req.postDataBuffer();assert.equal(sha(data),req.headers()['x-chunk-sha256']);
   if(index===1&&fail){await route.fulfill({status:503,json:{detail:'Injected upload interruption'}});return;}
   parts.set(index,data.length);body={part_index:index};
 } else if(url.pathname.endsWith('/complete')) {assert.equal(parts.size,3);complete=true;session.state='completed';body=session;}
 else if(url.pathname.endsWith('/u1')) body={...session,parts:[...parts].map(([part_index,size_bytes])=>({part_index,size_bytes}))};
 else throw new Error(`Unexpected ${req.method()} ${url.pathname}`);
 await route.fulfill({status:req.method()==='DELETE'?202:200,json:body});
});
try {
 await page.goto(origin+'/library');
 await page.getByRole('button',{name:'Open folder Projects',exact:true}).click();
 await expect(page.getByRole('button',{name:'Open folder Footage',exact:true})).toBeVisible();
 await expect(page.locator('.library-breadcrumbs')).toContainText('Projects');
 assert.ok((await page.getByRole('button',{name:'Open folder Footage'}).boundingBox()).height > 160);
 assert.equal(await page.locator('.library-drop,select').count(),0);
 await page.getByLabel('New folder name',{exact:true}).fill('References');
 await page.getByRole('button',{name:'Create folder',exact:true}).click();
 await expect(page.getByRole('button',{name:'Open folder References'})).toBeVisible();
 const textDrag=await page.evaluateHandle(()=>{const dt=new DataTransfer();dt.setData('text/plain','internal drag');return dt;});
 await page.locator('body').dispatchEvent('dragenter',{dataTransfer:textDrag});
 await expect(page.locator('.library-drag-overlay')).not.toBeVisible();
 const drag=await page.evaluateHandle(()=>{const dt=new DataTransfer();dt.items.add(new File(['drag probe'],'probe.mp4',{type:'video/mp4'}));return dt;});
 await page.locator('.library-topbar').dispatchEvent('dragenter',{dataTransfer:drag});
 await expect(page.locator('.library-drag-overlay')).toBeVisible();
 await expect(page.locator('.library-drop-path')).toHaveText('/Projects');
 await page.locator('.library-drag-frame').dispatchEvent('dragenter',{dataTransfer:drag});
 await page.locator('.library-drag-frame').dispatchEvent('dragleave',{dataTransfer:drag,clientX:100,clientY:100});
 await expect(page.locator('.library-drag-overlay')).toBeVisible();
 await page.keyboard.press('Escape');
 await expect(page.locator('.library-drag-overlay')).not.toBeVisible();
 await page.locator('.library-file-picker').setInputFiles({name:'resume.mp4',mimeType:'video/mp4',buffer});
 await expect(page.locator('.library-dialog[open]')).toContainText('/Projects');
 assert.equal(await page.locator('.library-dialog[open] select').count(),0);
 await expect(page.getByRole('checkbox',{name:/Offline node/})).toBeDisabled();
 await page.getByRole('checkbox',{name:/Studio GPU/}).check();
 await page.getByRole('button',{name:'Upload files',exact:true}).click();
 await expect(page.locator('.library-upload').getByRole('button',{name:'Retry upload'})).toBeVisible({timeout:30000});
 const before=calls.length, received=[...parts.keys()];fail=false;
 await page.reload();
 await expect(page.locator('.library-breadcrumbs')).toContainText('Projects');
 await expect(page.getByText('Select original file to resume',{exact:true})).toBeVisible();
 await page.locator('.library-upload input').setInputFiles({name:'wrong.mp4',mimeType:'video/mp4',buffer:Buffer.from('wrong')});
 await expect(page.locator('.library-upload [role=alert]')).toContainText('not the original file');
 await page.locator('.library-upload input').setInputFiles({name:'resume.mp4',mimeType:'video/mp4',buffer});
 await expect(page.locator('.library-upload [role=status]')).toHaveText('Ready',{timeout:30000});
 assert.deepEqual(calls.slice(before).sort(),[0,1,2].filter(i=>!received.includes(i)));
 assert.ok(await page.locator('.library-gallery > :first-child').evaluate(e=>e.classList.contains('library-folder-entry')));
 await page.getByRole('article',{name:'resume.mp4',exact:true}).getByRole('button',{name:'Delete resource',exact:true}).click();
 await page.locator('.library-delete-confirm').click();
 await expect(page.getByRole('article',{name:'resume.mp4',exact:true})).toContainText('Deleting · 1 device remaining');
 await page.locator('.library-breadcrumbs').getByRole('button',{name:'Library',exact:true}).click();
 await expect(page.getByRole('button',{name:'Open folder Projects',exact:true})).toBeVisible();
 await expect(page.getByRole('button',{name:'↑ Choose files',exact:true})).toBeEnabled();
 await page.setViewportSize({width:390,height:844});
 const mobileDrag=await page.evaluateHandle(()=>{const dt=new DataTransfer();for(const name of ['one.mp4','two.mp4'])dt.items.add(new File(['probe'],name,{type:'video/mp4'}));return dt;});
 await page.locator('body').dispatchEvent('dragover',{dataTransfer:mobileDrag});
 await expect(page.locator('.library-drag-overlay')).toBeVisible();
 const box=await page.locator('.library-drag-overlay').boundingBox();assert.equal(box.width,390);assert.equal(box.height,844);
 await page.locator('.library-drag-overlay').dispatchEvent('drop',{dataTransfer:mobileDrag});
 await expect(page.locator('.library-drag-overlay')).not.toBeVisible();
 await expect(page.locator('.library-dialog[open]')).toContainText('Choose devices');
 await expect(page.locator('.library-dialog[open]')).toContainText('one.mp4, two.mp4');
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 assert.deepEqual(errors,[]);
 console.log('PASS: folder navigation/create, Files-only global drag, nested dragleave, Escape, narrow overlay, fixed destination, offline devices, interrupted upload/reload/resume, SHA rejection, deletion state.');
} finally {await browser.close();}
