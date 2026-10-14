import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";

const live = process.env.HOLOLAB_LENS_SAMPLE ? JSON.parse(readFileSync(process.env.HOLOLAB_LENS_SAMPLE, "utf8")) : null;
const gpu = { index: 0, uuid: "gpu-a", name: "GPU", mem_used_mb: 23756, mem_total_mb: 24576, util_percent: 100 };
const sample = live?.lens ?? { ts: 1700000000, error: null, gpus: [gpu], processes: [
  { gpu_uuid: "gpu-a", pid: 20, name: "python", used_mb: 22000, owner: "external" },
  { gpu_uuid: "gpu-a", pid: 21, name: "python", used_mb: 1000, owner: "job", job_id: "job-a", job_name: "training" },
] };
const metrics = live?.metrics ?? { ts: 1700000000, cpu_percent: 61.9, mem_used_gb: 51, mem_total_gb: 64, gpus: [gpu] };

test("VRAM lens loads on demand, restores fleet, handles errors and themes", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", e => errors.push(e.message));
  await page.routeWebSocket("**", ws => ws.close());
  let calls = 0; let fail = false;
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = [];
    if (path.endsWith("/resources/vram")) {
      calls++;
      if (fail) return route.fulfill({ status: 504, contentType: "application/json", body: '{}' });
      body = sample;
    } else if (path === "/api/nodes") {
      body = (live ? ["node-a"] : ["node-a", "node-b", "node-c", "node-d"]).map(id => ({ node_id: id, node_name: live ? "Local GPU host · live sample" : id, gpu: { count: 1, name: sample.gpus[0]?.name, total_vram_gb: 24 }, packs: [], connected_ts: 0, advertised_url: null, workspace_root: null, legacy_workspace_roots: [], flops_executor_id: null, packs_dir: null }));
    } else if (path === "/api/nodes/metrics/history") {
      body = { sample_interval_s: 3, nodes: Object.fromEntries((live ? ["node-a"] : ["node-a", "node-b", "node-c", "node-d"]).map(id => [id, [metrics]])) };
    } else if (path === "/api/workflows/lens-test") {
      body = { workflow_id: "lens-test", name: "Resource lens preview", graph: { nodes: [], edges: [] }, created_ts: 1, updated_ts: 1 };
    } else if (path === "/api/jobs/job-a") {
      body = { job_id: "job-a", algorithm_name: "training", algorithm_version: "1", state: "running", params: {} };
    }
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.setViewportSize({width: 1440, height: 1000});
  await page.addInitScript(() => localStorage.setItem("hl-layout-bottom", "460"));
  await page.goto('/w/lens-test');
  const panel = page.locator('.hl-node-metrics');
  await expect(panel.locator('[data-pulse-node]')).toHaveCount(live ? 1 : 4);
  expect(calls).toBe(0);
  const initialScroll = await panel.evaluate(el => { el.scrollTop = 12; return el.scrollTop; });
  if (!live) expect(initialScroll).toBeGreaterThan(0);
  await panel.getByRole('button', {name: 'Open VRAM resource lens'}).first().evaluate(el => (el as HTMLElement).click());
  const lens = page.getByRole('region', {name: 'VRAM resource lens'});
  await expect(lens).toBeVisible();
  await expect(lens).not.toHaveAttribute('aria-busy', 'true');
  await expect(panel.locator('[data-pulse-node]')).toHaveCount(1);
  // React StrictMode may mount the effect twice in development; the server coalesces probes.
  expect(calls).toBeLessThanOrEqual(2);
  const settledCalls = calls;
  await page.waitForTimeout(300);
  expect(calls).toBe(settledCalls);
  if (!live) {
    await expect(lens).toContainText('External · 非 HoloLab');
    await lens.getByRole('button', {name:'training', exact:true}).click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(lens).toBeVisible();
  }
  for (const theme of ['light', 'dark']) {
    await page.evaluate(theme => document.documentElement.dataset.theme = theme, theme);
    await panel.screenshot({path:`test-results/resource-lens-${live ? 'live' : 'fixture'}-${theme}.png`});
  }
  await page.keyboard.press('Escape');
  await expect(lens).toHaveCount(0);
  await expect(panel.locator('[data-pulse-node]')).toHaveCount(live ? 1 : 4);
  expect(await panel.evaluate(el => el.scrollTop)).toBe(initialScroll);
  await expect(panel.getByRole('button', {name: 'Open VRAM resource lens'}).first()).toBeFocused();
  await panel.screenshot({path:`test-results/resource-lens-${live ? 'live' : 'fixture'}-returned.png`});
  await panel.getByRole('button', {name:'Open VRAM resource lens'}).first().click();
  await expect(lens).not.toHaveAttribute('aria-busy', 'true');
  // Exercise the same component at a narrow inspector width.

  // The inspector itself can be narrow independently of the page viewport.
  await panel.evaluate(el => { el.style.width='340px'; el.style.boxSizing='border-box'; el.style.height='100vh'; el.style.position='fixed'; el.style.inset='0'; el.style.zIndex='10000'; el.style.background='var(--surface)'; });
  expect(await panel.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
  await lens.screenshot({path:`test-results/resource-lens-${live ? 'live' : 'fixture'}-narrow.png`});
  fail = true;
  await lens.getByRole('button', {name:'Refresh', exact:true}).click();
  await expect(lens.getByRole('alert')).toContainText('Node did not reply');
  await lens.getByRole('button', {name:'All compute nodes', exact:false}).click();
  await expect(lens).toHaveCount(0);
  fail = false;
  await page.setViewportSize({width: 390, height: 844});
  await page.getByRole('tab', {name: 'Inspect', exact: true}).click();
  await panel.getByRole('button', {name:'Open VRAM resource lens'}).first().click();
  await expect(lens).not.toHaveAttribute('aria-busy', 'true');
  expect(await panel.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
  await lens.screenshot({path:`test-results/resource-lens-${live ? 'live' : 'fixture'}-mobile.png`});
  expect(errors).toEqual([]);

});
