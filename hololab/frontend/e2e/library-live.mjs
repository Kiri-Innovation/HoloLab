// Opt-in acceptance against a real Gateway and a locally accessible node library.
// Only the unique file created by this run is deleted.
import { chromium, expect } from '@playwright/test';
import { readFile, mkdir, writeFile, stat } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { join } from 'node:path';
import assert from 'node:assert/strict';
const file = process.env.LIBRARY_TEST_VIDEO;
if (!file) throw new Error('Set LIBRARY_TEST_VIDEO to a real MP4.');
const origin = process.env.HOLOLAB_URL || 'http://127.0.0.1:5187';
const nodeRoot = process.env.LIBRARY_NODE_ROOT || '/root/hololab/library';
const out = '/tmp/hololab-library-product';
await mkdir(out, { recursive: true });
const bytes = await readFile(file);
const digest = createHash('sha256').update(bytes).digest('hex');
const name = `product-library-${Date.now()}.mp4`;
const nodePath = join(nodeRoot, name);
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [];
page.on('pageerror', e => errors.push(e.message));
const requests = [];
page.on('response', r => { if (r.url().includes('/api/library/') && r.request().method() !== 'GET') requests.push({ method: r.request().method(), url: r.url(), status: r.status() }); });
try {
  await page.goto(origin);
  await page.getByRole('button', { name: 'Library', exact: true }).click();
  await expect(page).toHaveURL(origin + '/library');
  await expect(page.getByRole('button', { name: 'Choose files', exact: true })).toBeEnabled();
  const transfer = await page.evaluateHandle(({ data, name }) => {
    const dt = new DataTransfer();
    dt.items.add(new File([Uint8Array.from(atob(data), c => c.charCodeAt(0))], name, { type: 'video/mp4' }));
    return dt;
  }, { data: bytes.toString('base64'), name });
  await page.locator('.library-drop').dispatchEvent('dragover', { dataTransfer: transfer });
  await expect(page.locator('.library-drop')).toHaveClass(/is-dragging/);
  await page.locator('.library-drop').dispatchEvent('drop', { dataTransfer: transfer });
  await page.getByRole('checkbox', { name: /kiri4090/ }).check();
  await page.screenshot({ path: `${out}/destination.png`, fullPage: true });
  await page.getByRole('button', { name: 'Upload files', exact: true }).click();
  await expect(page.locator('.library-upload [role=status]')).toHaveText('Ready', { timeout: 120000 });
  const listing = await (await page.request.get(origin + '/api/library/resources?folder_id=root')).json();
  const resource = listing.resources.find(r => r.name === name);
  assert.equal(resource.status, 'ready');
  assert.equal(resource.placements[0].node_name, 'kiri4090');
  assert.equal(resource.placements[0].verified_sha256, digest);
  assert.equal((await stat(nodePath)).size, bytes.length);
  assert.equal(createHash('sha256').update(await readFile(nodePath)).digest('hex'), digest);
  for (const theme of ['light', 'dark']) {
    await page.evaluate(theme => localStorage.setItem('hololab.theme', theme), theme);
    await page.reload();
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    await expect(page.locator('tbody')).toContainText(name);
    await page.screenshot({ path: `${out}/library-${theme}.png`, fullPage: true });
  }
  const row = page.getByRole('row').filter({ hasText: name });
  await row.getByRole('button', { name: 'Delete resource', exact: true }).click();
  await expect(page.getByRole('dialog')).toContainText('kiri4090');
  await page.screenshot({ path: `${out}/delete-confirm.png`, fullPage: true });
  const deletion = page.waitForResponse(r => r.request().method() === 'DELETE' && r.url().endsWith(`/resources/${resource.resource_id}`));
  await page.locator('.library-delete-confirm').click();
  assert.equal((await deletion).status(), 202);
  await expect(row).toHaveCount(0, { timeout: 120000 });
  await expect.poll(async () => stat(nodePath).then(() => true, e => { if (e.code === 'ENOENT') return false; throw e; }), { timeout: 120000 }).toBe(false);
  assert.deepEqual(errors, []);
  const result = { name, resource_id: resource.resource_id, bytes: bytes.length, sha256: digest, nodePath, placement: resource.placements[0], deleted: true, requests };
  await writeFile(`${out}/live-result.json`, JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result, null, 2));
} finally { await browser.close(); }
