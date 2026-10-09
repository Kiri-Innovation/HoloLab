import { test, expect } from "@playwright/test";

const workflowId = "11111111-1111-1111-1111-111111111111";

for (const theme of ["light", "dark"] as const) {
  test(`reference quote matches adjacent node controls in ${theme} theme`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.addInitScript(theme => {
      localStorage.setItem("hololab.theme", theme);
      Object.defineProperty(window, "flops", { value: {
        version: 1,
        showDocument: async () => ({ success: true }),
        insertReference: async () => ({ success: true }),
      } });
    }, theme);
    await page.routeWebSocket("**/*", () => {});
    await page.route("**/api/**", async route => {
      const path = new URL(route.request().url()).pathname;
      let body: unknown = [];
      if (path === "/api/pack-catalog") body = [{
        name: "demo-echo", version: "0.1.0", manifest_hash: "demo", node_ids: [],
        description: "Example step", category: [], inputs: {}, outputs: {}, params: {}, arrayable: false,
      }];
      if (path === `/api/workflows/${workflowId}`) body = {
        workflow_id: workflowId, name: "Reference icon · workflow", created_ts: 1700000000, updated_ts: 1700000000,
        graph: {
          nodes: [0, 1].map(i => ({ id: `step-${i}`, algorithm_name: "demo-echo", algorithm_version: "0.1.0", position: { x: i * 330, y: i * 130 }, params: {}, assigned_node_id: null })),
          edges: [],
        },
      };
      if (path === "/api/nodes/metrics/history") body = { sample_interval_s: 5, nodes: {} };
      if (path === "/api/artifacts/summary") body = { total_bytes: 0, exclusive_bytes: 0, artifact_count: 0 };
      await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
    });
    await page.goto(`/w/${workflowId}`);
    await expect(page.locator(".react-flow__node")).toHaveCount(2);
    await expect(page.locator("[data-hl-toast-anchor]")).toHaveCount(1);
    const node = page.locator(".react-flow__node").first();
    const reference = node.getByRole("button", { name: "引用到 Flops", exact: true });
    const quote = reference.locator("svg");
    const source = node.locator("[data-hl-open-source] svg");
    await expect(source).toBeVisible();
    await expect(node.locator('button[title^="run this node"]')).toBeVisible();
    await expect(quote).toHaveAttribute("data-reference-icon", "quote");
    await expect(quote.locator("path")).toHaveCount(2);
    await expect(reference).toHaveAttribute("title", /^引用到 Flops:/);
    for (const attribute of ["width", "height", "stroke-width"]) {
      expect(await quote.getAttribute(attribute)).toBe(await source.getAttribute(attribute));
    }
    await expect(quote).toHaveAttribute("stroke", "currentColor");
    await node.screenshot({ path: testInfo.outputPath(`reference-quote-${theme}.png`), animations: "disabled" });
    await testInfo.attach(`reference-quote-${theme}`, { path: testInfo.outputPath(`reference-quote-${theme}.png`), contentType: "image/png" });
  });
}
