import {test,expect} from '@playwright/test';
test('serialises edits during a slow save, and Run waits for the newest value',async({page})=>{
  const writes:any[]=[];let release!:()=>void;
  await page.route('**/test-save',async r=>{writes.push(r.request().postDataJSON());if(writes.length===1)await new Promise<void>(resolve=>release=resolve);await r.fulfill({json:{workflow_id:'fixture',updated_ts:writes.length+1}});});
  await page.goto('/e2e/fixtures/draft-autosave.html');
  await page.getByLabel('Value',{exact:true}).fill('first');
  await expect.poll(()=>writes.length).toBe(1);
  await page.getByLabel('Value',{exact:true}).fill('second');
  await page.getByRole('button',{name:'Save before Run'}).click();
  await expect(page.getByLabel('Run gate')).toHaveText('none');
  release();
  await expect(page.getByLabel('Run gate')).toHaveText('ready');
  expect(writes).toEqual([{value:'first',base:1},{value:'second',base:2}]);
});
test('background flush, persistent failure feedback, retry and snapshot read-only',async({page})=>{
  const writes:any[]=[];let fail=true;
  await page.route('**/test-save',async r=>{writes.push(r.request().postDataJSON());await r.fulfill(fail?{status:503,json:{detail:'offline'}}:{json:{workflow_id:'fixture',updated_ts:2}});});
  await page.goto('/e2e/fixtures/draft-autosave.html');
  await page.getByLabel('Value',{exact:true}).fill('changed');
  await page.evaluate(()=>{Object.defineProperty(document,'visibilityState',{configurable:true,value:'hidden'});document.dispatchEvent(new Event('visibilitychange'));});
  await expect(page.getByLabel('Save status')).toHaveText('error');
  await page.waitForTimeout(1600);expect(writes).toHaveLength(1);
  await page.getByRole('button',{name:'Save before Run'}).click();
  await expect(page.getByLabel('Run gate')).toHaveText('blocked');
  fail=false;
  await page.getByRole('button',{name:'Save before Run'}).click();
  await expect(page.getByLabel('Save status')).toHaveText('saved');
  await page.getByRole('button',{name:'Toggle read only'}).click();
  const count=writes.length;
  await page.getByLabel('Value',{exact:true}).fill('snapshot change');await page.waitForTimeout(1000);
  expect(writes).toHaveLength(count);
});
test('beacon rejection warns instead of silently dropping a pending edit',async({page})=>{
  await page.route('**/test-save',r=>r.fulfill({json:{workflow_id:'fixture',updated_ts:2}}));
  await page.goto('/e2e/fixtures/draft-autosave.html');
  await page.getByLabel('Value',{exact:true}).fill('pending');
  expect(await page.evaluate(()=>{navigator.sendBeacon=()=>false;const e=new Event('beforeunload',{cancelable:true});window.dispatchEvent(e);return e.defaultPrevented;})).toBe(true);
});
test('HTTP 409 parks autosave and blocks Run without another write',async({page})=>{
  let writes=0;
  await page.route('**/test-save',async r=>{writes++;await r.fulfill({status:409,json:{detail:{code:'workflow_conflict',message:'Changed by another writer',base_updated_ts:1,current:{workflow_id:'fixture',name:'remote',updated_ts:2}}}});});
  await page.goto('/e2e/fixtures/draft-autosave.html');
  await page.getByLabel('Value',{exact:true}).fill('conflicting edit');
  await expect(page.getByLabel('Save status')).toHaveText('conflict');
  await page.getByLabel('Value',{exact:true}).fill('another local edit');
  await page.getByRole('button',{name:'Save before Run'}).click();
  await expect(page.getByLabel('Run gate')).toHaveText('blocked');
  await page.waitForTimeout(1000);expect(writes).toBe(1);
});
