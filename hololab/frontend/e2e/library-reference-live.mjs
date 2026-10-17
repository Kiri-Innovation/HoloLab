// Read-only live acceptance. Load the candidate build only in this browser;
// production assets/processes and all API responses remain untouched.
// LIBRARY_REFERENCE_DIST=/tmp/hololab-library-reference-dist node e2e/library-reference-live.mjs
import { chromium, expect } from '@playwright/test';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const origin = process.env.HOLOLAB_URL || 'http://117.50.186.141:8828';
const dist = process.env.LIBRARY_REFERENCE_DIST || '/tmp/hololab-library-reference-dist';
const out = process.env.LIBRARY_REFERENCE_REPORT || '/tmp/hololab-library-reference-report';
await mkdir(out, {recursive:true});
const browser = await chromium.launch();
const context = await browser.newContext({viewport:{width:1440,height:1000},locale:'zh-CN'});
const page = await context.newPage();
const errors = [];
page.on('pageerror', e => errors.push(e.message));
const report = {origin, candidateAssets:dist, api:'live, unmodified, read-only', realCoBrowser:false};
try {
  await page.goto(origin+'/library');
  await page.locator('.library-file-entry').first().click({button:'right'});
  report.productionMenu = await page.getByRole('menu').innerText();
  await page.keyboard.press('Escape');
  await page.route(origin+'/**', async route => {
    const req = route.request(); const url = new URL(req.url());
    if (url.pathname.startsWith('/api/') && req.method() !== 'GET') throw new Error(`Unexpected write: ${req.method()} ${url}`);
    if (req.resourceType() === 'document' && url.pathname === '/library') return route.fulfill({path:path.join(dist,'index.html'),contentType:'text/html'});
    if (url.pathname.startsWith('/assets/')) return route.fulfill({path:path.join(dist,url.pathname),contentType:url.pathname.endsWith('.css')?'text/css':'application/javascript'});
    return route.continue();
  });
  const resources = await (await page.request.get(origin+'/api/library/resources?folder_id=root')).json();
  const resource = resources.resources[0];
  assert.ok(resource);
  report.resource = {id:resource.resource_id, name:resource.name};
  for (const theme of ['light','dark']) {
    await page.evaluate(theme => localStorage.setItem('hololab.theme',theme),theme);
    await page.reload();
    await expect(page.locator('html')).toHaveAttribute('data-theme',theme);
    const card = page.locator(`[data-resource-id="${resource.resource_id}"]`);
    await card.click({button:'right'});
    await expect(page.getByRole('menuitem',{name:'复制引用',exact:true})).toBeVisible();
    await page.screenshot({path:`${out}/menu-${theme}.png`,fullPage:true});
    await page.getByRole('menuitem',{name:'复制引用',exact:true}).click();
    await expect(page.locator('.hl-toast-pill')).toContainText('已复制引用');
    await expect(page.locator('.hl-toast-pill')).toHaveCSS('opacity', '1');
    await expect(page.locator('.hl-toast-pill')).toBeInViewport();
    await page.screenshot({path:`${out}/copied-${theme}.png`,fullPage:true});
    // Paste the actual browser clipboard; do not stub writeText or execCommand.
    await page.evaluate(() => {const t=document.createElement('textarea');t.id='clipboard-probe';document.body.append(t);t.focus();});
    await page.keyboard.press('Control+V');
    const text = await page.locator('#clipboard-probe').inputValue();
    assert.ok(text.includes(`hololab://library-resource/${resource.resource_id}\n`));
    assert.ok(text.includes(resource.name));
    assert.ok(text.includes(`${resource.size_bytes} bytes`));
    assert.ok(text.includes('kiri4090'));
    assert.ok(text.includes(origin+'/api/resolve?ref='));
    report.clipboard = text;
    report.secureContext = await page.evaluate(() => window.isSecureContext);
    await page.locator('#clipboard-probe').evaluate(e => e.remove());
  }
  report.folderMenu = 'skipped: no live folder card; covered by unit/API tests';
  const folder = page.locator('.library-folder-entry').first();
  if (await folder.count()) {
    await folder.click({button:'right'});
    await page.getByRole('menuitem',{name:'复制引用',exact:true}).click();
    await expect(page.locator('.hl-toast-pill')).toContainText('已复制引用');
    report.folderMenu = 'passed';
  }
  // Contract checks only: this injection is NOT a real CoBrowser host.
  await page.evaluate(() => {window.__references=[];window.flops={version:1,insertReference:async p=>{window.__references.push(p);return {success:true};}};});
  await page.locator(`[data-resource-id="${resource.resource_id}"]`).click({button:'right'});
  await page.getByRole('menuitem',{name:'复制引用',exact:true}).click();
  await expect(page.locator('.hl-toast-pill')).toContainText('已插入 Flops 输入框');
  report.simulatedBridgePayload = await page.evaluate(() => window.__references);
  assert.equal(report.simulatedBridgePayload.length,1);
  assert.equal(report.simulatedBridgePayload[0].type,'library_resource');
  await page.evaluate(() => {window.flops.insertReference=async()=>({success:false,reason:'unsupported-kind'});});
  await page.locator(`[data-resource-id="${resource.resource_id}"]`).click({button:'right'});
  await page.getByRole('menuitem',{name:'复制引用',exact:true}).click();
  await expect(page.locator('.hl-toast-pill')).toContainText('unsupported-kind。已复制，可粘贴到对话');
  report.simulatedRejectionFallback = 'passed';
  assert.deepEqual(errors,[]);
  await writeFile(`${out}/result.json`,JSON.stringify(report,null,2));
  console.log(JSON.stringify(report,null,2));
} finally {await browser.close();}
