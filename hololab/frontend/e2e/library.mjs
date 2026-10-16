import { chromium, expect } from '@playwright/test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
const origin = process.env.HOLOLAB_URL || 'http://127.0.0.1:5173';
const out = '/tmp/hololab-library'; await mkdir(out, { recursive: true });
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors=[]; page.on('pageerror', e=>errors.push(e.message));
const CHUNK=8*1024**2;
const buffer=Buffer.alloc(CHUNK*3+17, 42);
const sha=b=>createHash('sha256').update(b).digest('hex');
let session=null, resource=null, fail=true, complete=false, nodeAvailable=true;
const parts=new Map(); const calls=[];
await page.route('**/api/**', async route=>{
 const request=route.request(), u=new URL(request.url()); const path=u.pathname;
 let body={};
 if(path==='/api/nodes') body=[...(nodeAvailable ? [{node_id:'online',node_name:'Studio GPU'}] : []),{node_id:'offline',node_name:'Offline node',online:false}];
 else if(path==='/api/library/folders') body={folders:[]};
 else if(path==='/api/library/resources') body={resources:resource?[{...resource,status:complete?'ready':'uploading',ready_count:complete?1:0,target_count:1,placements:[{node_id:'online',node_name:'Studio GPU',online:1,desired_state:'present',observed_state:complete?'ready':'pending'}],transfers:[]}]:[]};
 else if(path==='/api/library/uploads' && request.method()==='POST') {
   const data=request.postDataJSON(); assert.equal(data.sha256,sha(buffer)); assert.deepEqual(data.target_node_ids,['online']);
   session={upload_id:'u1',resource_id:'r1',version:1,expected_size:buffer.length,expected_sha256:data.sha256,chunk_size:CHUNK,state:'uploading'};
   resource={resource_id:'r1',name:data.filename,folder_id:'root',resource_type:'generic',size_bytes:data.size_bytes}; body=session;
 } else if(path==='/api/library/uploads') body={sessions:session?[session]:[]};
 else if(path.includes('/parts/')) {
   const index=Number(path.split('/').at(-1)); const data=request.postDataBuffer(); calls.push(index);
   assert.equal(sha(data),request.headers()['x-chunk-sha256']); assert.deepEqual(data,buffer.subarray(index*CHUNK,(index+1)*CHUNK));
   if(index===1&&fail){await route.fulfill({status:503,json:{detail:'Temporary test interruption'}});return;}
   parts.set(index,data.length);body={part_index:index};
 } else if(path.endsWith('/complete')) {assert.equal(parts.size,4);complete=true; session.state='completed';body=session;}
 else if(path.endsWith('/u1')) body={...session,parts:[...parts].map(([part_index,size_bytes])=>({part_index,size_bytes}))};
 else throw new Error(`Unexpected ${request.method()} ${path}`);
 await route.fulfill({json:body});
});
try {
 await page.goto(origin+'/library');
 await expect(page.getByRole('button',{name:'Choose files',exact:true})).toBeEnabled();
 await page.locator('.library-drop input').setInputFiles({name:'resume-test.mp4',mimeType:'video/mp4',buffer});
 await expect(page.getByRole('checkbox',{name:/Offline node/})).toBeDisabled();
 await page.getByRole('checkbox',{name:/Studio GPU/}).check(); await page.getByRole('button',{name:'Upload files',exact:true}).click();
 await expect(page.locator('.library-upload').getByRole('button',{name:'Retry upload'})).toBeVisible({timeout:30000});
 const prior=[...parts.keys()]; assert.ok(prior.length>0); fail=false;
 await page.reload();
 await expect(page.getByText('Select original file to resume',{exact:true})).toBeVisible();
 await page.locator('.library-upload input').setInputFiles({name:'wrong.mp4',mimeType:'video/mp4',buffer:Buffer.from('wrong')});
 await expect(page.locator('.library-upload [role=alert]')).toContainText('not the original file');
 assert.equal(complete,false);
 const before=calls.length;
 await page.locator('.library-upload input').setInputFiles({name:'resume-test.mp4',mimeType:'video/mp4',buffer});
 await expect(page.locator('.library-upload [role=status]')).toHaveText('Ready',{timeout:30000});
 assert.deepEqual(calls.slice(before).sort(),[0,1,2,3].filter(i=>!prior.includes(i)));
 assert.equal(await page.locator('html').getAttribute('lang'),'en');
 const bounds=await page.locator('.library-page').boundingBox(); assert.ok(bounds.width>800);
 for(const theme of ['light','dark']) {
   await page.evaluate(theme => { localStorage.setItem('hololab.theme', theme); }, theme);
   await page.reload();
   await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
   await expect(page.locator('tbody')).toContainText('resume-test.mp4');
   await page.screenshot({path:`${out}/product-mock-${theme}.png`,fullPage:true});
 }
 // Multi-file selection opens one destination dialog; cancelling creates no sessions.
 await page.goto(origin+'/library');
 await expect(page.getByRole('button',{name:'Choose files',exact:true})).toBeEnabled();
 await page.locator('.library-drop input').setInputFiles([{name:'a.mp4',mimeType:'video/mp4',buffer:Buffer.from('a')},{name:'b.mp4',mimeType:'video/mp4',buffer:Buffer.from('b')}]);
 await expect(page.getByRole('dialog')).toContainText('a.mp4, b.mp4');
 await expect(page.getByRole('button',{name:'Upload files',exact:true})).toBeDisabled();
 await page.getByRole('button',{name:'Cancel',exact:true}).click();
 await expect(page.getByRole('dialog')).not.toBeVisible();
 nodeAvailable=false;
 await page.getByRole('button',{name:'Refresh',exact:true}).click();
 // Let the inventory refresh finish, then verify a disconnected known node
 // cannot remain selected as an upload destination.
 await page.waitForResponse(r=>r.url().includes('/api/library/uploads') && r.request().method()==='GET');
 await page.locator('.library-drop input').setInputFiles({name:'offline.mp4',mimeType:'video/mp4',buffer:Buffer.from('offline')});
 await expect(page.getByRole('checkbox',{name:/Studio GPU/})).toBeDisabled();
 await page.getByRole('button',{name:'Cancel',exact:true}).click();
 await page.setViewportSize({width:390,height:844});
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.screenshot({path:`${out}/narrow.png`,fullPage:true});
 assert.deepEqual(errors,[]);console.log('PASS: chunk hashes, 3-way upload, interruption, refresh recovery, wrong-file rejection, missing-parts-only resume, offline targets, themes and narrow layout');
} finally { await browser.close(); }
