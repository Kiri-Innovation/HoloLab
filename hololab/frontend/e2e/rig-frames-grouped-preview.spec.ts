import { expect, test, type Route } from "@playwright/test";

const WORKFLOW_ID = "wf-rig-frames-grouped";
const SNAPSHOT_ID = "snap-rig-frames-grouped";
const NODE_ID = "node-rig";
const GRAPH_NODE_ID = "temporal-grouping";
const HANDLE_ID = "grouped-frames";
const CREATED_TS = 1_700_000_000;
const GROUPS = [
  { name: "g000001", aliases: ["cam0", "cam1", "cam2", "cam3", "cam4", "cam5", "cam7"], tCenterNs: 1_789_705_641_085_140_224 },
  { name: "g000002", aliases: ["cam0", "cam2", "cam3", "cam5", "cam7"], tCenterNs: 1_789_705_641_118_473_472 },
  { name: "g000003", aliases: ["cam0", "cam2", "cam4", "cam5"], tCenterNs: 1_789_705_641_151_806_976 },
];

function groupEntry(group: (typeof GROUPS)[number]) {
  return {
    name: group.name,
    is_dir: true,
    size_bytes: null,
    entry_count: group.aliases.length,
    children: group.aliases.map((alias) => ({ name: `${alias}.jpg`, is_dir: false, size_bytes: 1 })),
  };
}

function jsonRoute(body: unknown) {
  return async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  };
}

const pack = {
  name: "rig-temporal-grouping",
  version: "0.1.2",
  manifest_hash: "hash",
  node_ids: [NODE_ID],
  description: "group rig frames",
  category: [],
  docs: null,
  source_entry: null,
  manifest_path: null,
  source_dir: null,
  inputs: {},
  outputs: {
    frames_grouped: {
      tags: ["rig_frames_grouped"],
      arrayed: false,
      preview: null,
      description: null,
      dim_labels: null,
    },
  },
  params: {},
  arrayable: false,
};

async function stubPage(page: import("@playwright/test").Page) {
  await page.route("**/_thumb/**", async (route) => {
    await route.fulfill({
      contentType: "image/gif",
      body: Buffer.from("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7", "base64"),
    });
  });
  await page.route("**/api/pack-catalog", jsonRoute([pack]));
  await page.route("**/api/nodes", jsonRoute([{
    node_id: NODE_ID,
    node_name: "rig-node",
    gpu: { total: 0, gpus: [] },
    packs: [{ name: pack.name, version: pack.version, manifest_hash: "hash" }],
    connected_ts: CREATED_TS,
    advertised_url: null,
    workspace_root: "/ws",
    legacy_workspace_roots: [],
    flops_executor_id: null,
    packs_dir: "/packs",
    pack_dirs: ["/packs"],
  }]));
  await page.route("**/api/nodes/metrics/history**", jsonRoute({ sample_interval_s: 5, nodes: {} }));
  await page.route("**/api/jobs", jsonRoute([]));
  await page.route("**/api/artifacts/summary", jsonRoute({ total_bytes: 0, exclusive_bytes: 0, artifact_count: 0 }));
  await page.route("**/api/workflows", jsonRoute([]));
  const graph = {
    nodes: [{
      id: GRAPH_NODE_ID,
      algorithm_name: pack.name,
      algorithm_version: pack.version,
      // Keep the expanded preview wholly inside the screenshot viewport.
      position: { x: 100, y: -100 },
      params: {},
      assigned_node_id: NODE_ID,
    }],
    edges: [],
  };
  await page.route(`**/api/workflows/${WORKFLOW_ID}`, jsonRoute({
    workflow_id: WORKFLOW_ID, name: "rig grouped preview", graph, created_ts: CREATED_TS, updated_ts: CREATED_TS,
  }));
  await page.route(`**/api/workflows/${WORKFLOW_ID}/runs`, jsonRoute([{
    snapshot_id: SNAPSHOT_ID, workflow_id: WORKFLOW_ID, created_ts: CREATED_TS,
    node_count: 1, job_count: 1, state_counts: { done: 1 }, overall_state: "done", favorite: false, note: null,
  }]));
  await page.route(`**/api/snapshots/${SNAPSHOT_ID}`, jsonRoute({
    snapshot_id: SNAPSHOT_ID, workflow_id: WORKFLOW_ID, created_ts: CREATED_TS, graph,
    jobs: [{
      job_id: "job-done", workflow_id: WORKFLOW_ID, node_id: NODE_ID, graph_node_id: GRAPH_NODE_ID,
      algorithm_name: pack.name, algorithm_version: pack.version, state: "done", progress: null,
      fail_reason: null, fail_exit_code: null, fail_message: null, params: {}, input_handles: {},
      output_handles: { frames_grouped: HANDLE_ID }, parent_job_id: null, shard_element_id: null,
      shard_element_ids: null, expected_shards: null, created_ts: CREATED_TS, updated_ts: CREATED_TS + 1,
    }],
  }));
  await page.route(`**/api/handles/${HANDLE_ID}`, jsonRoute({
    handle_id: HANDLE_ID, node_id: NODE_ID, storage: "dir", tags: ["rig_frames_grouped"],
    size_bytes: 0, output_port_name: "frames_grouped", proxy_url: `/proxy/${HANDLE_ID}/`,
    absolute_path: "/ws/grouped", deleted_ts: null, preview: null, dim_labels: null, dim_sizes: null,
  }));
  await page.route(`**/api/handles/${HANDLE_ID}/summary`, jsonRoute({
    handle_id: HANDLE_ID, kind: "dir", tags: ["rig_frames_grouped"], storage: "dir", size_bytes: 0,
    proxy_url: `/proxy/${HANDLE_ID}/`, absolute_path: "/ws/grouped",
    fields: { entries: GROUPS.map(groupEntry) },
  }));
  for (const group of GROUPS) {
    await page.route(`**/proxy/${HANDLE_ID}/${group.name}/group.json`, jsonRoute({
      group_index: Number(group.name.slice(1)),
      t_center_ns: group.tCenterNs,
      n_cameras: group.aliases.length,
      frames: group.aliases.map((alias) => ({ alias, file: `${alias}.jpg` })),
    }));
  }
}

test("rig_frames_grouped renders one complete camera group per row", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 1400 });
  // Exercise LazyThumb's documented non-observer fallback so every mocked
  // thumbnail performs a real image decode in this layout test.
  await page.addInitScript(() => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    delete (window as any).IntersectionObserver;
  });
  await stubPage(page);
  await page.goto(`/w/${WORKFLOW_ID}`);
  const card = page.locator('[data-hololab-node="algorithm"]').first();
  await expect(card).toBeVisible({ timeout: 15_000 });
  await card.locator('button[title="expand preview"]').click();
  const preview = card.locator("[data-hl-nested-strip]");
  await expect(preview).toBeVisible();
  const rows = preview.locator("[data-hl-group-row]");
  await expect(rows).toHaveCount(3);
  await expect(rows.nth(0).locator("[data-hl-group-time]")).not.toHaveText("时间读取中");
  await expect(rows.nth(0).locator("[data-hl-group-cameras]")).toHaveText("7 cams");
  await expect(rows.nth(1).locator("[data-hl-group-cameras]")).toHaveText("5 cams");
  await expect(rows.nth(2).locator("[data-hl-group-cameras]")).toHaveText("4 cams");
  await expect(rows.nth(0).locator("[data-hl-group-image]")).toHaveCount(7);
  await expect(rows.nth(1).locator("[data-hl-group-image]")).toHaveCount(5);
  await expect(rows.nth(2).locator("[data-hl-group-image]")).toHaveCount(4);
  const geometry = await rows.evaluateAll((els) => els.map((row) => {
    const rowRect = row.getBoundingClientRect();
    const images = Array.from(row.querySelectorAll("[data-hl-group-image]")).map((image) => {
      const rect = image.getBoundingClientRect();
      return { x: rect.x, y: rect.y, width: rect.width };
    });
    return { y: rowRect.y, images };
  }));
  expect(geometry[0].y).toBeLessThan(geometry[1].y);
  expect(geometry[1].y).toBeLessThan(geometry[2].y);
  for (const row of geometry) {
    expect(new Set(row.images.map((image) => image.y.toFixed(3))).size).toBe(1);
    expect(new Set(row.images.map((image) => image.width.toFixed(3))).size).toBe(1);
  }
  const firstImage = rows.nth(0).locator("img").first();
  await expect(firstImage).toHaveCSS("object-fit", "cover");
  await expect.poll(async () => rows.locator("img").evaluateAll(
    (images) => images.every((image) => image.complete && image.naturalWidth > 0),
  )).toBe(true);
  const previewMetrics = await preview.evaluate((el) => ({
    clientHeight: el.clientHeight,
    scrollHeight: el.scrollHeight,
    rect: el.getBoundingClientRect().toJSON(),
  }));
  console.log({ geometry, previewMetrics });
  await preview.screenshot({ path: "test-results/rig-frames-grouped-list.png" });
});
