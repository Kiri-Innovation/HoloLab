// Run against an isolated Gateway/node: LIBRARY_E2E_VIDEO and LIBRARY_E2E_FIXTURE_ROOT are required.
import {mkdirSync,writeFileSync,readFileSync,existsSync} from 'node:fs';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const out=process.env.LIBRARY_E2E_OUTPUT || '/tmp/hololab-library-recursive';mkdirSync(out,{recursive:true});
const base=process.env.LIBRARY_E2E_BASE || 'http://127.0.0.1:8838', root=process.env.LIBRARY_E2E_FIXTURE_ROOT, video=process.env.LIBRARY_E2E_VIDEO, log=[];
assert(root && video, 'Set LIBRARY_E2E_FIXTURE_ROOT and LIBRARY_E2E_VIDEO for the isolated fixture');
const browser=await chromium.launch();
try {
for (const [locale,theme] of [['en-US','light'],['en-US','dark'],['zh-CN','light'],['zh-CN','dark']]) {
 const context=await browser.newContext({viewport:{width:1200,height:850},locale}); const page=await context.newPage();
 page.on('response',async r=>{if(r.url().includes('/api/library/') && (r.request().method()!=='GET'||r.url().includes('delete-preview')))log.push({method:r.request().method(),path:new URL(r.url()).pathname+new URL(r.url()).search,status:r.status()});});
 const name=`Recursive acceptance ${locale} ${theme}`;
 const response=await page.request.post(base+'/api/library/folders',{data:{name}});assert.equal(response.status(),201);const folder=await response.json();
 const child=await (await page.request.post(base+'/api/library/folders',{data:{name:'Footage',parent_id:folder.folder_id}})).json();
 await page.goto(base+'/library');await page.evaluate(t=>localStorage.setItem('hololab.theme',t),theme);await page.reload();
 await page.getByRole('button',{name:`Open folder ${name}`,exact:true}).click();
 await page.getByRole('button',{name:'Open folder Footage',exact:true}).click();
 await page.locator('.library-file-picker').setInputFiles({name:'library-demo.mp4',mimeType:'video/mp4',buffer:readFileSync(video)});
 await page.getByRole('checkbox',{name:/Temporary library node/}).check();await page.getByRole('button',{name:'Upload files',exact:true}).click();
 await expect(page.getByRole('article',{name:'library-demo.mp4',exact:true})).toContainText('Ready',{timeout:30000});
 const resources=(await (await page.request.get(base+'/api/library/resources?folder_id='+child.folder_id)).json()).resources;
 assert.equal(resources.length,1);const rid=resources[0].resource_id;
 const physical=root+'/node/'+name+'/Footage/library-demo.mp4';assert(existsSync(physical));
 await page.locator('.library-breadcrumbs').getByRole('button',{name:'Library',exact:true}).click();
 await page.getByRole('button',{name:`Open folder ${name}`,exact:true}).click({button:'right'});await page.getByRole('menuitem',{name:'Delete',exact:true}).click();
 const zh=locale.startsWith('zh');await page.getByRole('button',{name:zh?'删除文件夹':'Delete folder',exact:true}).click();
 const dialog=page.getByRole('dialog',{name:zh?'连同内容一起删除？':'Delete folder with contents?',exact:true});await expect(dialog).toBeVisible();await expect(dialog).toContainText(zh?'1 个资源、1 个子文件夹':'1 resource and 1 subfolder');
 await expect(page.getByRole('button',{name:zh?'全部删除':'Delete everything',exact:true})).toBeEnabled();
 await page.screenshot({path:`${out}/confirm-${locale}-${theme}.png`});
 // Also verify narrow-screen modal containment before committing the deletion.
 if(locale==='zh-CN'&&theme==='dark') {await page.setViewportSize({width:390,height:844});await page.screenshot({path:`${out}/confirm-mobile.png`});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));}
 const resultPromise=page.waitForResponse(r=>r.request().method()==='DELETE'&&r.url().includes('?recursive=true'));
 await page.getByRole('button',{name:zh?'全部删除':'Delete everything',exact:true}).click();
 const result=await resultPromise;assert.equal(result.status(),202);const operation=await result.json();assert.equal(operation.resource_count,1);assert.equal(operation.subfolder_count,1);
 await expect(page.getByRole('button',{name:`Open folder ${name}`,exact:true})).toHaveCount(0,{timeout:30000});
 await expect.poll(async()=> (await (await page.request.get(base+'/api/library/operations/'+operation.operation_id)).json()).state).toBe('completed');
 assert.equal((await page.request.get(base+'/api/library/resources/'+rid)).status(),404);assert(!existsSync(physical));assert(!existsSync(root+'/node/'+name));
 await page.screenshot({path:`${out}/empty-${locale}-${theme}.png`});
 log.push({locale,theme,folder_id:folder.folder_id,resource_id:rid,delete_status:result.status(),resource_count:operation.resource_count,subfolder_count:operation.subfolder_count,operation_id:operation.operation_id,state:'completed',resource_get:404,physical_file_absent:true,physical_directory_absent:true});
 await context.close();
}
console.log(JSON.stringify(log,null,2));
} finally {writeFileSync(out+'/trace.json',JSON.stringify(log,null,2));await browser.close();}
