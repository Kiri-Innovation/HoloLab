import { expect, test, type Route } from "@playwright/test";

const W = "wf-view";
const S = "snap-view";
const N = "node-a";
const graph = {
  nodes: [
    { id: "producer", kind: "algorithm", algorithm_name: "producer", algorithm_version: "1", position: { x: 30, y: 80 }, params: {}, assigned_node_id: N },
    { id: "sticker", kind: "view", view_type: "artifact-preview", title: "第二输出", position: { x: 380, y: 80 } },
  ],
  edges: [{ id: "observe-second", source: "producer", sourceHandle: "second", target: "sticker", targetHandle: "in" }],
};
const json = (body: unknown) => async (route: Route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });

test("view sticker survives drag, save, reload, selection and deletion without an algorithm pack", async ({ page }) => {
  const jobs: unknown[] = [];
  let persistedGraph = structuredClone(graph);
  const saves: Array<{ graph: typeof graph }> = [];
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await page.route("**/api/pack-catalog", json([{ name: "producer", version: "1", manifest_hash: "x", node_ids: [N], description: "", category: [], inputs: {}, outputs: { first: { tags: ["image"], storage: "dir", preview: null }, second: { tags: ["rig_timeline"], storage: "dir", preview: null } }, params: {}, arrayable: false }]));
  await page.route("**/api/nodes", json([{ node_id: N, node_name: N, gpu: { total: 0, gpus: [] }, packs: [], connected_ts: 0, advertised_url: null, workspace_root: "/ws", legacy_workspace_roots: [], flops_executor_id: null, packs_dir: "/packs", pack_dirs: [] }]));
  await page.route("**/api/nodes/metrics/history**", json({ sample_interval_s: 5, nodes: {} }));
  await page.route("**/api/jobs", json(jobs));
  await page.route("**/api/artifacts/summary", json({ total_bytes: 0, exclusive_bytes: 0, artifact_count: 0 }));
  await page.route("**/api/workflows**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === `/api/workflows/${W}`) {
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({ workflow_id: W, name: "view", graph: persistedGraph, created_ts: 1, updated_ts: saves.length + 1 }) });
    }
    if (path !== "/api/workflows" || route.request().method() !== "POST") {
      return route.fulfill({ contentType: "application/json", body: JSON.stringify([]) });
    }
    const body = route.request().postDataJSON() as { graph: typeof graph };
    persistedGraph = body.graph;
    saves.push(body);
    return route.fulfill({ contentType: "application/json", body: JSON.stringify({ workflow_id: W, updated_ts: saves.length + 1 }) });
  });
  await page.route(`**/api/workflows/${W}/runs`, json([{ snapshot_id: S, workflow_id: W, created_ts: 1, node_count: 1, job_count: 1, state_counts: { done: 1 }, overall_state: "done", favorite: false, note: null }]));
  await page.route(`**/api/snapshots/${S}`, json({ snapshot_id: S, workflow_id: W, created_ts: 1, graph, jobs: [{ job_id: "only-producer-job", workflow_id: W, node_id: N, graph_node_id: "producer", algorithm_name: "producer", algorithm_version: "1", state: "done", progress: null, fail_reason: null, params: {}, input_handles: {}, output_handles: { second: "h-second" }, parent_job_id: null, shard_element_id: null, created_ts: 1, updated_ts: 1 }] }));
  await page.route("**/api/handles/h-second", json({ handle_id: "h-second", node_id: N, proxy_url: "/proxy/n/timeline", storage: "dir", absolute_path: "/ws/timeline", deleted_ts: null, tags: ["rig_timeline"], preview: null, dim_labels: null, dim_sizes: null }));
  await page.route("**/proxy/n/timeline/**", (route) => route.fulfill({ status: 200, contentType: "image/png", body: "" }));
  await page.goto(`/w/${W}`);
  await expect(page.getByText("第二输出")).toBeVisible({ timeout: 15_000 });
  // Let the autosave hook record the hydrated graph as its clean baseline;
  // a real operator cannot drag before this hydration turn either.
  await page.waitForTimeout(1_000);
  await expect(page.getByText("第二输出").locator("..")).toContainText("第二输出");
  const sticker = page.locator('[data-hl-view-node="sticker"]');
  const header = sticker.locator(".hl-view-drag-handle");
  const before = await sticker.boundingBox();
  expect(before).not.toBeNull();
  const headerBox = await header.boundingBox();
  expect(headerBox).not.toBeNull();
  await page.mouse.move(headerBox!.x + 20, headerBox!.y + 10);
  await page.mouse.down();
  await page.mouse.move(headerBox!.x + 125, headerBox!.y + 70, { steps: 8 });
  await page.mouse.up();
  await expect.poll(async () => (await sticker.boundingBox())?.x ?? before!.x).not.toBe(before!.x);
  await expect.poll(() => saves.length).toBeGreaterThan(0);
  const savedSticker = persistedGraph.nodes.find((n) => n.id === "sticker");
  expect(savedSticker?.position).not.toEqual(graph.nodes[1].position);
  await page.screenshot({ path: "test-results/view-node-dragged.png" });

  await page.reload();
  await expect(sticker).toBeVisible();
  await page.waitForTimeout(1_000);
  const rfSticker = page.getByTestId("rf__node-sticker");
  await rfSticker.click({ button: "right" });
  await rfSticker.click();
  await page.getByTestId("rf__node-producer").click({ modifiers: ["Control"] });
  await expect(rfSticker).toHaveClass(/selected/);
  await rfSticker.click();
  await expect(rfSticker).toHaveClass(/selected/);
  await page.keyboard.press("Backspace");
  await expect(sticker).toHaveCount(0);
  expect(pageErrors).toEqual([]);
  expect(jobs).toHaveLength(0);
});
