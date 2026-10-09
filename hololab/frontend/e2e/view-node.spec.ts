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

test("view sticker reads a producer's second output without creating a job", async ({ page }) => {
  const jobs: unknown[] = [];
  await page.route("**/api/pack-catalog", json([{ name: "producer", version: "1", manifest_hash: "x", node_ids: [N], description: "", category: [], inputs: {}, outputs: { first: { tags: ["image"], storage: "dir", preview: null }, second: { tags: ["rig_timeline"], storage: "dir", preview: null } }, params: {}, arrayable: false }]));
  await page.route("**/api/nodes", json([{ node_id: N, node_name: N, gpu: { total: 0, gpus: [] }, packs: [], connected_ts: 0, advertised_url: null, workspace_root: "/ws", legacy_workspace_roots: [], flops_executor_id: null, packs_dir: "/packs", pack_dirs: [] }]));
  await page.route("**/api/nodes/metrics/history**", json({ sample_interval_s: 5, nodes: {} }));
  await page.route("**/api/jobs", json(jobs));
  await page.route("**/api/artifacts/summary", json({ total_bytes: 0, exclusive_bytes: 0, artifact_count: 0 }));
  await page.route("**/api/workflows", json([]));
  await page.route(`**/api/workflows/${W}`, json({ workflow_id: W, name: "view", graph, created_ts: 1, updated_ts: 1 }));
  await page.route(`**/api/workflows/${W}/runs`, json([{ snapshot_id: S, workflow_id: W, created_ts: 1, node_count: 1, job_count: 1, state_counts: { done: 1 }, overall_state: "done", favorite: false, note: null }]));
  await page.route(`**/api/snapshots/${S}`, json({ snapshot_id: S, workflow_id: W, created_ts: 1, graph, jobs: [{ job_id: "only-producer-job", workflow_id: W, node_id: N, graph_node_id: "producer", algorithm_name: "producer", algorithm_version: "1", state: "done", progress: null, fail_reason: null, params: {}, input_handles: {}, output_handles: { second: "h-second" }, parent_job_id: null, shard_element_id: null, created_ts: 1, updated_ts: 1 }] }));
  await page.route("**/api/handles/h-second", json({ handle_id: "h-second", node_id: N, proxy_url: "/proxy/n/timeline", storage: "dir", absolute_path: "/ws/timeline", deleted_ts: null, tags: ["rig_timeline"], preview: null, dim_labels: null, dim_sizes: null }));
  await page.route("**/proxy/n/timeline/**", (route) => route.fulfill({ status: 200, contentType: "image/png", body: "" }));
  await page.goto(`/w/${W}`);
  await expect(page.getByText("第二输出")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("第二输出").locator("..")).toContainText("第二输出");
  await page.screenshot({ path: "/tmp/hololab-view-node.png" });
  expect(jobs).toHaveLength(0);
});
