import { chromium, expect } from '@playwright/test';
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
const origin = process.env.WEBSITE_URL || 'http://127.0.0.1:5317';
const out = '/tmp/hololab-library-stage5';
await mkdir(out, {recursive:true});
const browser = await chromium.launch();
try {
 for (const language of ['en','zh-CN']) for (const theme of ['light','dark']) {
  const page = await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[]; page.on('pageerror',e=>errors.push(e.message));
  let deleting=false, calls=0, offset=0, conflict=false;
  const item={resource_id:'test-resource',name:'library-e2e-12mb.bin',folder_id:'root',resource_type:'generic',size_bytes:12582912,ready_count:1,target_count:2,transfers:[],placements:[{node_id:'A',node_name:'Studio A',online:1,desired_state:'present',observed_state:'ready'},{node_id:'B',node_name:'Travel B',online:0,desired_state:'present',observed_state:'pending'}]};
  await page.route('**/api/**',async route=>{
   const req=route.request(),url=new URL(req.url());let json;
   if(req.method()==='DELETE') {calls++;deleting=true;json={...item,status:'deleting',remaining_nodes:1};}
   else if(url.pathname==='/api/library/resources') {offset=Number(url.searchParams.get('offset'));json={total:51,resources:[{...item,name:offset?'page-51.bin':item.name,status:deleting?'deleting':'partially_ready',remaining_nodes:deleting?1:2,placements:item.placements.map(p=>({...p,desired_state:deleting?'absent':'present',observed_state:deleting?(p.node_id==='A'?'absent':'deleting'):p.observed_state}))}]};}
   else if(url.pathname==='/api/library/folders') json={folders:[]};
   else if(url.pathname==='/api/nodes') json=[{node_id:'A',node_name:'Studio A',online:true},{node_id:'B',node_name:'Travel B',online:false}];
   else if(url.pathname==='/api/library/uploads' && req.method()==='POST') {conflict=true;await route.fulfill({status:409,json:{detail:'A file or folder with this name already exists; overwriting is not allowed'}});return;}
   else if(url.pathname==='/api/library/uploads') json={sessions:[]};
   else throw new Error(`Unexpected ${req.method()} ${url.pathname}`);
   await route.fulfill({json,status:req.method()==='DELETE'?202:200});
  });
  await page.goto(origin+(language==='en'?'':'/zh-CN')+'/library');
  await page.locator('.theme-control').selectOption(theme);
  await expect(page.locator('tbody')).toContainText(language==='en'?'Generic file':'通用文件');
  await page.getByRole('button',{name:language==='en'?'Next':'下一页',exact:true}).click();
  await expect(page.locator('tbody')).toContainText('page-51.bin'); assert.equal(offset,50);
  await page.getByRole('button',{name:language==='en'?'Previous':'上一页',exact:true}).click();
  await expect(page.locator('tbody')).toContainText(item.name);
  await page.locator('tbody .library-delete').click();
  const dialog=page.getByRole('dialog');
  await expect(dialog).toContainText('/'+item.name);
  await expect(dialog).toContainText('12.0 MiB');
  await expect(dialog).toContainText('Travel B');
  assert.equal(await dialog.locator('.library-delete-confirm').evaluate(e => getComputedStyle(e).backgroundColor), 'rgb(180, 35, 50)');
  await page.screenshot({path:`${out}/confirm-${language}-${theme}.png`,fullPage:true});
  await dialog.locator('.library-delete-confirm').click();
  await expect(dialog).not.toBeVisible();
  await expect(page.locator('tbody')).toContainText(language==='en'?'Deleting · 1 device remaining':'删除中 · 还有 1 台设备持有');
  assert.equal(calls,1);
  await page.screenshot({path:`${out}/deleting-${language}-${theme}.png`,fullPage:true});
  await page.locator('.library-drop input').setInputFiles({name:item.name,mimeType:'application/octet-stream',buffer:Buffer.from('duplicate')});
  await page.getByRole('checkbox',{name:/Studio A/}).check();
  await page.getByRole('button',{name:language==='en'?'Upload files':'开始上传',exact:true}).click();
  await expect(page.locator('.library-upload [role=alert]')).toContainText(language==='en'?'Choose another name':'请更换名称');
  assert.equal(conflict,true); assert.deepEqual(errors,[]);
  await page.close();
 }
 console.log('PASS: delete confirmation, offline remaining count, pagination, generic display, readable 409; EN/ZH × light/dark screenshots');
} finally {await browser.close();}
