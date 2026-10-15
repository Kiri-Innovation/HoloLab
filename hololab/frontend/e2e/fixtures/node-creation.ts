import { test as base, type Route } from "@playwright/test";

export const catalog = [
  { name: "image-resize", category: ["image", "transform"] },
  { name: "image-blend", category: ["image", "compose"] },
  ...Array.from({ length: 20 }, (_, i) => ({ name: `utility-${String(i).padStart(2, "0")}`, category: ["utilities"] })),
].map(pack => ({
  ...pack, version: "1.0.0", manifest_hash: "fixture", node_ids: ["compute"],
  description: "Node creation fixture", inputs: { in: { tags: ["image"] } }, outputs: { out: { tags: ["image"] } }, params: {}, arrayable: false,
}));

// Exercise the production App and drop conversion using the API fixture pattern.
export const test = base.extend<{ editor: void }>({
  editor: [async ({ page }, use) => {
    const json = (body: unknown) => (route: Route) =>
      route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
    await page.routeWebSocket("**/*", () => {});
    await page.route("**/api/**", json([]));
    await page.route("**/api/pack-catalog", json(catalog));
    await page.route("**/api/nodes", json([]));
    await page.route("**/api/nodes/metrics/history**", json({ sample_interval_s: 5, nodes: {} }));
    await page.route("**/api/artifacts/summary", json({ total_bytes: 0, exclusive_bytes: 0, artifact_count: 0 }));
    await page.route("**/api/workflows/menu-fixture", json({
      workflow_id: "menu-fixture", name: "Node creation", graph: { nodes: [], edges: [] }, created_ts: 1, updated_ts: 1,
    }));
    await page.route("**/api/workflows", json({ workflow_id: "menu-fixture", updated_ts: 2 }));
    await page.goto("/w/menu-fixture");
    await page.locator(".hl-pack-palette .hl-pack-leaf").first().waitFor();
    await page.locator(".react-flow__pane").waitFor();
    await use();
  }, { auto: true }],
});
export { expect } from "@playwright/test";
