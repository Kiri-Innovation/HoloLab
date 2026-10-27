import { expect, test } from "@playwright/test";

const pack = { name: "cache/demo", version: "1.0", manifest_hash: "cache-demo", description: "", inputs: {}, outputs: {}, params: {}, node_ids: ["compute"], arrayable: false };

test("keeps cached capability evidence visible across a reload", async ({ page }, testInfo) => {
  await page.routeWebSocket("**", () => {});
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = [];
    if (path === "/api/pack-catalog") body = [pack];
    else if (path === "/api/nodes") body = [{ node_id: "compute", node_name: "Compute", packs: [pack], gpu: { count: 0 } }];
    else if (path === "/api/nodes/metrics/history") body = { nodes: {} };
    else if (path === "/api/capabilities/check") {
      await new Promise(resolve => setTimeout(resolve, 600));
      body = { nodes: { "cache/demo@1.0": { status: "satisfied", source: "cache/demo@1.0", node_id: "compute", issues: [] } } };
    }
    await route.fulfill({ json: body });
  });

  const start = Date.now();
  await page.goto("/packs");
  await expect(page.getByText("环境检查中…", { exact: true })).toBeVisible();
  await expect(page.getByText("已声明的可探测项满足", { exact: true })).toBeVisible();
  const coldMs = Date.now() - start;

  const reloadStart = Date.now();
  await page.reload();
  await expect(page.getByText("已声明的可探测项满足", { exact: true })).toBeVisible();
  await expect(page.getByText("环境检查中…", { exact: true })).toHaveCount(0);
  const warmMs = Date.now() - reloadStart;
  await page.screenshot({ path: testInfo.outputPath("capability-cache-reload.png"), fullPage: true });

  // The intentionally delayed response gives this assertion a full background
  // refresh window, not merely a one-frame check immediately after reload.
  await page.waitForTimeout(700);
  await expect(page.getByText("环境检查中…", { exact: true })).toHaveCount(0);
  expect(coldMs).toBeGreaterThanOrEqual(900);
  // Reload measures the whole Pack Manager hydration, not just the cache read;
  // it must still finish before the intentionally delayed check response.
  expect(warmMs).toBeLessThan(750);
  console.log(`capability cache timings: cold=${coldMs}ms warm=${warmMs}ms`);
});
