import { expect, test } from '@playwright/test';

test('running viewer discovers successive frames without an index or output handle, retries, and carries progress', async ({ page }) => {
  let iteration = 100;
  let polls = 0;
  let logPolls = 0;
  let offline = false;
  const bases: string[] = [];
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/jobs/current/log?**', route => {
    logPolls++;
    if (offline) return route.fulfill({ status: 503 });
    return route.fulfill({ json: { lines: [{ line: `[ITER ${iteration}/1000]`, stream: 'stdout', ts: 1 }] } });
  });
  await page.route(/\/(?:proxy|scoped-pack-assets)\//, route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/viewer.html')) return route.fulfill({ contentType: 'text/html', body: `
      <pre id="state"></pre><script>addEventListener('message', e => {
        if(e.data?.type === 'hololab-viewer-state') document.querySelector('#state').textContent = JSON.stringify(e.data);
      });</script>` });
    if (url.pathname.endsWith('/_glob')) {
      bases.push(url.searchParams.get('base')!);
      polls++;
      // One broken pattern must not discard the other pattern's frames.
      if (offline || url.searchParams.get('pattern')!.startsWith('previews/')) return route.fulfill({ status: 404 });
      return route.fulfill({ json: { files: [0, iteration].map(i => `save_cam/iter${i}_ssim.png`) } });
    }
    return route.fulfill({ status: 404 }); // No history index throughout training.
  });
  await page.goto('/e2e/fixtures/live-node-viewer.html');
  const state = () => page.frameLocator('iframe').locator('#state').textContent().then(s => JSON.parse(s!));
  await expect.poll(async () => (await state()).progress?.current).toBe(100);
  await expect.poll(async () => (await state()).frames.map((f: { iteration: number }) => f.iteration)).toEqual([0, 100]);
  expect((await state()).running).toBe(true);
  expect(bases.every(base => base === 'w/workflow/j/current/state')).toBe(true);
  offline = true;
  const before = polls;
  await expect.poll(() => polls).toBeGreaterThan(before + 2);
  expect((await state()).frames).toHaveLength(2);
  offline = false;
  iteration = 200;
  await expect.poll(async () => (await state()).frames.map((f: { iteration: number }) => f.iteration)).toEqual([0, 100, 200]);
  await expect.poll(async () => (await state()).progress?.current).toBe(200);
  expect((await state()).running).toBe(true); // Still running when the second new frame arrives.
  await page.reload();
  await expect.poll(async () => (await state()).progress?.current).toBe(200);
  await expect.poll(async () => (await state()).frames.map((f: { iteration: number }) => f.iteration)).toEqual([0, 200]);
  await page.getByText('Native progress', { exact: true }).click();
  await expect.poll(async () => (await state()).progress?.current).toBe(800);
  await page.getByText('Finish', { exact: true }).click();
  await expect.poll(async () => (await state()).running).toBe(false);
  const stoppedPolls = polls, stoppedLogs = logPolls;
  await page.waitForTimeout(1500);
  expect(polls).toBe(stoppedPolls);
  expect(logPolls).toBe(stoppedLogs);
  expect(errors).toEqual([]);
});
