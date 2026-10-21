import { test, expect } from '@playwright/test';
import fs from 'node:fs';
test('Runs references copy or insert a compact snapshot using the live read-only API', async ({page, context, request}) => {
  test.skip(!process.env.RUN_REF_ORIGIN, 'Requires an explicitly selected local preview and the read-only STG instance on port 8828');
  const origin = process.env.RUN_REF_ORIGIN || 'http://127.0.0.1:5173';
  const wf = '3711480d-e858-453c-bf5b-76af602e9805';
  const runs = await (await request.get('http://127.0.0.1:8828/api/workflows/'+wf+'/runs')).json();
  const id = runs[0].snapshot_id;
  await context.grantPermissions(['clipboard-read','clipboard-write']);
  await page.route('**/api/**', route => ['GET','HEAD'].includes(route.request().method()) ? route.continue() : route.fulfill({status:403,json:{detail:'Read-only verification'}}));
  await page.setViewportSize({width:1440,height:1000});
  await page.goto(origin+'/w/'+wf);
  await page.getByRole('tab',{name:'Runs',exact:true}).click();
  const row = page.locator('[data-hl-run-id="'+id+'"]');
  const button = row.getByRole('button',{name:'复制引用给 AI',exact:true});
  await button.click();
  await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe('hololab://run/'+id);
  const resolved = await (await request.get('http://127.0.0.1:8828/api/resolve',{params:{ref:'hololab://run/'+id}})).json();
  expect(resolved.resource.snapshot_id).toBe(id);
  expect((await request.get('http://127.0.0.1:8828/api/snapshots/'+id)).ok()).toBe(true);
  await expect(page).toHaveURL(origin+'/w/'+wf);
  await page.addInitScript(() => {
    (window as any).__references=[];
    (window as any).flops={version:1,insertReference:async (payload:any)=>{(window as any).__references.push(payload);return {success:true};}};
  });
  await page.reload();
  await page.getByRole('tab',{name:'Runs',exact:true}).click();
  await row.getByRole('button',{name:'引用到 Flops',exact:true}).click();
  await expect.poll(()=>page.evaluate(()=>(window as any).__references.length)).toBe(1);
  const payload=await page.evaluate(()=>(window as any).__references[0]);
  expect(payload.provider).toBe('hololab');expect(payload.type).toBe('run');
  expect(payload.resource.uri).toBe(origin+'/api/snapshots/'+id);
  const text=payload.snapshot.text;
  for(const section of ['identity','state','context','navigation'])expect(text).toContain('## '+section);
  expect(text).toContain('external:hololab:run');
  expect(text).not.toContain('"params"');expect(text).not.toContain('"output_handles"');
  fs.mkdirSync('/tmp/run-reference-review',{recursive:true});
  fs.writeFileSync('/tmp/run-reference-review/payload.json',JSON.stringify(payload,null,2));
  for(const theme of ['light','dark']){
    await page.evaluate(t=>document.documentElement.dataset.theme=t,theme);
    await page.waitForTimeout(250);
    await page.locator('.hl-inspector-sidebar').screenshot({path:'/tmp/run-reference-review/'+theme+'.png'});
  }
  // Old/unavailable resolver: insertion remains explicit about missing state.
  await page.route('**/api/resolve?**',route=>route.fulfill({status:404,json:{detail:'old gateway'}}));
  await row.getByRole('button',{name:'引用到 Flops',exact:true}).click();
  await expect.poll(()=>page.evaluate(()=>(window as any).__references.length)).toBe(2);
  expect(await page.evaluate(()=>(window as any).__references[1].snapshot.text)).toContain('"resolver_status": "unavailable"');
  await page.setViewportSize({width:600,height:900});
  await page.getByRole('tab',{name:'Runs',exact:true}).click();
  await expect(row.getByRole('button',{name:'引用到 Flops',exact:true})).toBeVisible();
  await page.screenshot({path:'/tmp/run-reference-review/narrow.png'});
});
