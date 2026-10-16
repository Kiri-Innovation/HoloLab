// Opt-in live acceptance: creates a dedicated folder and uploads one synthetic MP4.
import { chromium, expect } from '@playwright/test';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
const file=process.env.LIBRARY_TEST_VIDEO;
if(!file) throw new Error('Set LIBRARY_TEST_VIDEO to a synthetic MP4. This test creates a real resource.');
const origin=process.env.WEBSITE_URL||'http://127.0.0.1:5199';
const output='/tmp/hololab-library'; await mkdir(output,{recursive:true});
const browser=await chromium.launch(); const page=await browser.newPage({viewport:{width:1440,height:1100}});
const buffer=await readFile(file); let uploadId;
page.on('response',async response=>{if(response.url().endsWith('/api/library/uploads')&&response.request().method()==='POST'&&response.ok())uploadId=(await response.json()).upload_id;});
try {
 await page.goto(origin+'/library');
 await expect(page.getByRole('button',{name:'Choose files',exact:true})).toBeEnabled();
 const folder=`Website acceptance ${Date.now()}`;
 await page.getByRole('textbox',{name:'New folder name'}).fill(folder);
 await page.getByRole('button',{name:'Create folder',exact:true}).click();
 await expect(page.locator('.library-toolbar select option').filter({hasText:folder})).toHaveCount(1);
 await page.locator('.library-toolbar select').selectOption({label:'/'+folder});
 const selected=await page.locator('.library-toolbar select').inputValue();
 const transfer=await page.evaluateHandle(({data,name})=>{const dt=new DataTransfer();dt.items.add(new File([Uint8Array.from(atob(data),c=>c.charCodeAt(0))],name,{type:'video/mp4'}));return dt;},{data:buffer.toString('base64'),name:'library-demo.mp4'});
 await page.locator('.library-drop').dispatchEvent('dragover',{dataTransfer:transfer});
 for(const theme of ['light','dark']){await page.locator('.theme-control').selectOption(theme);await page.waitForTimeout(250);await page.screenshot({path:`${output}/live-en-drag-${theme}.png`,fullPage:true});}
 await page.locator('.library-drop').dispatchEvent('drop',{dataTransfer:transfer});
 await page.getByRole('checkbox').first().check();
 await page.screenshot({path:`${output}/live-destination.png`,fullPage:true});
 // Hold the real PUT briefly to capture an actual in-flight request, not fabricated progress.
 let release;const gate=new Promise(resolve=>{release=resolve;});
 await page.route('**/api/library/uploads/*/parts/*',async route=>{await gate;await route.continue();});
 await page.getByRole('button',{name:'Upload files',exact:true}).click();
 await expect(page.locator('.library-upload [role=status]')).toHaveText('Uploading',{timeout:30000});
 for(const theme of ['light','dark']){await page.locator('.theme-control').selectOption(theme);await page.waitForTimeout(250);await page.screenshot({path:`${output}/live-en-upload-${theme}.png`,fullPage:true});}
 const zh=await browser.newPage({viewport:{width:1440,height:1100}});
 await zh.goto(origin+'/zh-CN/library');
 await expect(zh.locator('.library-toolbar select option').filter({hasText:folder})).toHaveCount(1);
 await zh.locator('.library-toolbar select').selectOption(selected);
 await expect(zh.locator('.library-upload')).toContainText('library-demo.mp4');
 for(const theme of ['light','dark']){await zh.locator('.theme-control').selectOption(theme);await zh.waitForTimeout(250);await zh.screenshot({path:`${output}/live-zh-CN-upload-${theme}.png`,fullPage:true});}
 await zh.close(); release();
 await expect(page.locator('.library-upload [role=status]')).toHaveText('Ready',{timeout:120000});
 const session=await (await page.request.get(origin+`/api/library/uploads/${uploadId}`)).json();
 const resource=await (await page.request.get(origin+`/api/library/resources/${session.resource_id}`)).json();
 assert.equal(resource.status,'ready'); assert.equal(resource.size_bytes,buffer.length);
 await writeFile(`${output}/live-result.json`,JSON.stringify({uploadId,folder_id:selected,resource},null,2));
 for(const locale of ['en','zh-CN'])for(const theme of ['light','dark']){
   await page.goto(origin+(locale==='en'?'':'/zh-CN')+'/library');await page.locator('.theme-control').selectOption(theme);await page.waitForTimeout(250);
   await expect(page.locator('.library-toolbar select option').filter({hasText:folder})).toHaveCount(1);
   await page.locator('.library-toolbar select').selectOption(selected);await expect(page.locator('tbody')).toContainText('library-demo.mp4');
   await page.screenshot({path:`${output}/live-${locale}-ready-${theme}.png`,fullPage:true});
   if(locale==='zh-CN') {const fresh=await page.evaluateHandle(()=>new DataTransfer());await page.locator('.library-drop').dispatchEvent('dragover',{dataTransfer:fresh});await page.screenshot({path:`${output}/live-${locale}-drag-${theme}.png`,fullPage:true});await page.locator('.library-drop').dispatchEvent('dragleave');}
 }
 console.log(JSON.stringify({uploadId,resource_id:resource.resource_id,status:resource.status,bytes:resource.size_bytes,placements:resource.placements.map(p=>({node:p.node_name,state:p.observed_state,hash:p.verified_sha256}))}));
}finally{await browser.close();}
