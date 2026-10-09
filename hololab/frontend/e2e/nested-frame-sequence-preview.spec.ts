import { expect, test, type Route } from "@playwright/test";

const WORKFLOW_ID = "wf-nested-frame-sequence";
const SNAPSHOT_ID = "snap-nested-frame-sequence";
const NODE_ID = "node-nested";
const GRAPH_NODE_ID = "frame-extraction";
const HANDLE_ID = "nested-frames";
const GROUPS = [
  { name: "element-01", imageCount: 7, listedImageCount: 7 },
  { name: "element-02", imageCount: 7, listedImageCount: 7 },
  // The shared server drill budget can end mid-directory: its badge knows
  // the real count but only two filenames reach the viewer.
  { name: "element-03", imageCount: 7, listedImageCount: 2 },
  { name: "element-04", imageCount: 7, listedImageCount: 7 },
];

function jsonRoute(body: unknown) {
  return async (route: Route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  };
}

async function stubPage(page: import("@playwright/test").Page) {
  const pack = {
    name: "frame-extraction", version: "1.0.0", manifest_hash: "hash", node_ids: [NODE_ID],
    description: "generic nested frames", category: [], docs: null, source_entry: null, manifest_path: null,
    source_dir: null, inputs: {}, params: {}, arrayable: false,
    outputs: { frames: { tags: ["frame_sequence"], arrayed: true, preview: null, description: null, dim_labels: null } },
  };
  const graph = { nodes: [{ id: GRAPH_NODE_ID, algorithm_name: pack.name, algorithm_version: pack.version, position: { x: 100, y: -100 }, params: {}, assigned_node_id: NODE_ID }], edges: [] };
  await page.route("**/_thumb/**", async (route) => {
    await route.fulfill({ contentType: "image/gif", body: Buffer.from("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7", "base64") });
  });
  await page.route("**/api/pack-catalog", jsonRoute([pack]));
  await page.route("**/api/nodes", jsonRoute([{ node_id: NODE_ID, node_name: "nested-node", gpu: { total: 0, gpus: [] }, packs: [{ name: pack.name, version: pack.version, manifest_hash: "hash" }], connected_ts: 1_700_000_000, advertised_url: null, workspace_root: "/ws", legacy_workspace_roots: [], flops_executor_id: null, packs_dir: "/packs", pack_dirs: ["/packs"] }]));
  await page.route("**/api/nodes/metrics/history**", jsonRoute({ sample_interval_s: 5, nodes: {} }));
  await page.route("**/api/jobs", jsonRoute([]));
  await page.route("**/api/artifacts/summary", jsonRoute({ total_bytes: 0, exclusive_bytes: 0, artifact_count: 0 }));
  await page.route("**/api/workflows", jsonRoute([]));
  await page.route(`**/api/workflows/${WORKFLOW_ID}`, jsonRoute({ workflow_id: WORKFLOW_ID, name: "nested preview", graph, created_ts: 1_700_000_000, updated_ts: 1_700_000_000 }));
  await page.route(`**/api/workflows/${WORKFLOW_ID}/runs`, jsonRoute([{ snapshot_id: SNAPSHOT_ID, workflow_id: WORKFLOW_ID, created_ts: 1_700_000_000, node_count: 1, job_count: 1, state_counts: { done: 1 }, overall_state: "done", favorite: false, note: null }]));
  await page.route(`**/api/snapshots/${SNAPSHOT_ID}`, jsonRoute({ snapshot_id: SNAPSHOT_ID, workflow_id: WORKFLOW_ID, created_ts: 1_700_000_000, graph, jobs: [{ job_id: "job-done", workflow_id: WORKFLOW_ID, node_id: NODE_ID, graph_node_id: GRAPH_NODE_ID, algorithm_name: pack.name, algorithm_version: pack.version, state: "done", progress: null, fail_reason: null, fail_exit_code: null, fail_message: null, params: {}, input_handles: {}, output_handles: { frames: HANDLE_ID }, parent_job_id: null, shard_element_id: null, shard_element_ids: null, expected_shards: null, created_ts: 1_700_000_000, updated_ts: 1_700_000_001 }] }));
  await page.route(`**/api/handles/${HANDLE_ID}`, jsonRoute({ handle_id: HANDLE_ID, node_id: NODE_ID, storage: "dir", tags: ["frame_sequence"], size_bytes: 0, output_port_name: "frames", proxy_url: `/proxy/${HANDLE_ID}/`, absolute_path: "/ws/nested", deleted_ts: null, preview: null, dim_labels: null, dim_sizes: null }));
  await page.route(`**/api/handles/${HANDLE_ID}/summary`, jsonRoute({ handle_id: HANDLE_ID, kind: "dir", tags: ["frame_sequence"], storage: "dir", size_bytes: 0, proxy_url: `/proxy/${HANDLE_ID}/`, absolute_path: "/ws/nested", dim_sizes: [113, 7], fields: { entry_count: 115, truncated: true, entries: GROUPS.map(({ name, imageCount, listedImageCount }) => ({ name, is_dir: true, size_bytes: null, entry_count: imageCount, children: Array.from({ length: listedImageCount }, (_, i) => ({ name: `frame-${i + 1}.jpg`, is_dir: false, size_bytes: 1 })) })) } }));
}

test("arrayed<frame_sequence> shows at most three groups in one vertical column", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 1400 });
  await page.addInitScript(() => { delete (window as { IntersectionObserver?: unknown }).IntersectionObserver; });
  await stubPage(page);
  await page.goto(`/w/${WORKFLOW_ID}`);
  const card = page.locator('[data-hololab-node="algorithm"]').first();
  await expect(card).toBeVisible({ timeout: 15_000 });
  await card.locator('button[title="expand preview"]').click();
  const preview = card.locator("[data-hl-nested-strip]");
  await expect(preview).toBeVisible();
  const groups = preview.locator("[data-hl-group-card]");
  await expect(groups).toHaveCount(3);
  await expect(preview.locator("[data-hl-group-count-label]")).toHaveText("共 113 组");
  await expect(preview.getByText("+110 隐藏")).toBeVisible();
  await expect(preview.locator('[data-hl-group-card="element-03"]')).toHaveCount(0);
  await expect(preview.locator('[data-hl-group-card="element-01"] img')).toHaveCount(3);
  await expect(preview.locator('[data-hl-group-card="element-02"] img')).toHaveCount(3);
  await expect(preview.locator('[data-hl-group-card="element-04"] img')).toHaveCount(3);
  await preview.screenshot({ path: process.env.LAYOUT_BASELINE ? "test-results/nested-frame-sequence-before.png" : "test-results/nested-frame-sequence-after.png" });
  const boxes = await groups.evaluateAll((cards) => cards.map((card) => card.getBoundingClientRect().toJSON()));
  if (process.env.LAYOUT_BASELINE) {
    expect(boxes[0].y).toBe(boxes[1].y);
  } else {
    expect(new Set(boxes.map((box) => box.x.toFixed(3))).size).toBe(1);
    expect(boxes[0].y).toBeLessThan(boxes[1].y);
    expect(boxes[1].y).toBeLessThan(boxes[2].y);
    expect(new Set(boxes.map((box) => box.width.toFixed(3))).size).toBe(1);
  }
});
