// Visual regression / screenshot capture for the node-card header +
// footer refactor. Renders one AlgorithmNode fixture in three timing
// states — never-ran, mid-run, and terminal-done — and saves a screenshot
// of each. The three shots are the primary artefact the PR review reads
// against the design brief; the DOM assertions guard the header
// (pack.name + compute-node · version) and the footer (elapsed +
// relative-time + progress) so a future refactor can't silently break
// the split.

import { test, expect, type Route } from "@playwright/test";

const WORKFLOW_ID = "wf-card-refactor";
const SNAPSHOT_ID = "snap-card-refactor";
const GRAPH_NODE_ID = "image-undistort-1";
const NODE_ID = "kiri4090";
const PACK_NAME = "image-undistort";
const PACK_VERSION = "0.4.0";
// Fixed reference wall clock — the spec pins Date.now via page.clock so
// "3 分钟前" is deterministic across CI runs. Anchor 12 minutes past the
// jobs' updated_ts so the relative label reads "12分钟前".
const NOW_S = 1_700_000_000;
const RUN_STARTED = NOW_S - 12 * 60 - 26; // 12m 26s ago
const RUN_UPDATED = NOW_S - 12 * 60;      // 12m ago (elapsed = 26s)

function pack() {
  return {
    name: PACK_NAME,
    version: PACK_VERSION,
    manifest_hash: "hash",
    node_ids: [NODE_ID],
    description: "undistort",
    category: [],
    docs: null,
    source_entry: null,
    manifest_path: null,
    source_dir: null,
    inputs: {
      image: {
        tags: ["image"],
        required: true,
        arrayed: false,
        description: null,
      },
    },
    outputs: {
      image: {
        tags: ["image"],
        arrayed: false,
        preview: null,
        description: null,
        dim_labels: null,
      },
    },
    params: {},
    arrayable: false,
  };
}

function graph() {
  return {
    nodes: [
      {
        id: GRAPH_NODE_ID,
        algorithm_name: PACK_NAME,
        algorithm_version: PACK_VERSION,
        position: { x: 200, y: 160 },
        params: {},
        assigned_node_id: NODE_ID,
        parallelism: 1,
      },
    ],
    edges: [],
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

async function stubCommon(page: import("@playwright/test").Page) {
  await page.route("**/api/pack-catalog", jsonRoute([pack()]));
  await page.route("**/api/nodes", jsonRoute([
    {
      node_id: NODE_ID,
      node_name: "kiri4090",
      gpu: { total: 0, gpus: [] },
      packs: [{ name: PACK_NAME, version: PACK_VERSION, manifest_hash: "hash" }],
      connected_ts: NOW_S - 3600,
      advertised_url: null,
      workspace_root: "/ws",
      legacy_workspace_roots: [],
      flops_executor_id: null,
      packs_dir: "/packs",
      pack_dirs: ["/packs"],
    },
  ]));
  await page.route("**/api/nodes/metrics/history**", jsonRoute({
    sample_interval_s: 5,
    nodes: {},
  }));
  await page.route("**/api/jobs", jsonRoute([]));
  await page.route("**/api/artifacts/summary", jsonRoute({
    total_bytes: 0,
    exclusive_bytes: 0,
    artifact_count: 0,
  }));
  await page.route("**/api/workflows", jsonRoute([]));
  await page.route(`**/api/workflows/${WORKFLOW_ID}`, jsonRoute({
    workflow_id: WORKFLOW_ID,
    name: "card-refactor",
    graph: graph(),
    created_ts: NOW_S - 3600,
    updated_ts: NOW_S - 3600,
  }));
}

async function stubRuns(
  page: import("@playwright/test").Page,
  overallState: string,
  stateCounts: Record<string, number>,
) {
  await page.route(`**/api/workflows/${WORKFLOW_ID}/runs`, jsonRoute([
    {
      snapshot_id: SNAPSHOT_ID,
      workflow_id: WORKFLOW_ID,
      created_ts: RUN_STARTED,
      node_count: 1,
      job_count: 1,
      state_counts: stateCounts,
      overall_state: overallState,
      favorite: false,
      note: null,
    },
  ]));
}

async function stubSnapshotJobs(
  page: import("@playwright/test").Page,
  jobs: unknown[],
) {
  await page.route(`**/api/snapshots/${SNAPSHOT_ID}`, jsonRoute({
    snapshot_id: SNAPSHOT_ID,
    workflow_id: WORKFLOW_ID,
    created_ts: RUN_STARTED,
    graph: graph(),
    jobs,
  }));
}

// Screenshot only the node card — the drag surface around it is noise.
async function screenshotCard(
  page: import("@playwright/test").Page,
  path: string,
) {
  // Wait for the algorithm node body to render — the async chain
  // (workflows → snapshots → jobs → aggregator → node render) takes a
  // beat on cold load.
  const card = page.locator('[data-hololab-node="algorithm"]').first();
  await card.waitFor({ state: "visible", timeout: 15_000 });
  // Give the react-flow layer a paint tick so borders + shadows settle.
  await page.waitForTimeout(200);
  await card.screenshot({ path });
}

test.describe("Node-card header/footer refactor — visual states", () => {
  test.beforeEach(async ({ page }) => {
    await stubCommon(page);
  });

  test("never-ran state — footer reads 未运行", async ({ page }) => {
    await stubRuns(page, "done", {});
    // Empty jobs → no runtime attribution → footer shows 未运行.
    await stubSnapshotJobs(page, []);
    await page.goto(`/w/${WORKFLOW_ID}`);
    const card = page.locator('[data-hololab-node="algorithm"]').first();
    await expect(card).toBeVisible({ timeout: 15_000 });
    await expect(card).toContainText("kiri4090 · v0.4.0");
    await expect(card).toContainText("未运行");
    await screenshotCard(page, "test-results/card-never-ran.png");
  });

  test("running state — footer reads 已跑 <elapsed>", async ({ page }) => {
    await page.clock.install({ time: new Date(NOW_S * 1000) });
    await stubRuns(page, "running", { running: 1 });
    await stubSnapshotJobs(page, [
      {
        job_id: "j-run",
        workflow_id: WORKFLOW_ID,
        node_id: NODE_ID,
        graph_node_id: GRAPH_NODE_ID,
        algorithm_name: PACK_NAME,
        algorithm_version: PACK_VERSION,
        state: "running",
        progress: { current: 47, total: 100 },
        fail_reason: null,
        fail_exit_code: null,
        fail_message: null,
        params: {},
        input_handles: {},
        output_handles: null,
        parent_job_id: null,
        shard_element_id: null,
        expected_shards: null,
        shard_element_ids: null,
        // 26 s ago wall-clock → "已跑 26s"
        started_ts: NOW_S - 26,
        created_ts: NOW_S - 26,
        updated_ts: NOW_S - 5,
      },
    ]);
    await page.goto(`/w/${WORKFLOW_ID}`);
    const card = page.locator('[data-hololab-node="algorithm"]').first();
    await expect(card).toBeVisible({ timeout: 15_000 });
    await expect(card).toContainText("kiri4090 · v0.4.0");
    await expect(card).toContainText(/已跑 \d+s/);
    await expect(card).toContainText("47/100");
    await screenshotCard(page, "test-results/card-running.png");
  });

  test("done state — footer reads <elapsed> · <relative>", async ({ page }) => {
    await page.clock.install({ time: new Date(NOW_S * 1000) });
    await stubRuns(page, "done", { done: 1 });
    await stubSnapshotJobs(page, [
      {
        job_id: "j-done",
        workflow_id: WORKFLOW_ID,
        node_id: NODE_ID,
        graph_node_id: GRAPH_NODE_ID,
        algorithm_name: PACK_NAME,
        algorithm_version: PACK_VERSION,
        state: "done",
        progress: { current: 100, total: 100 },
        fail_reason: null,
        fail_exit_code: null,
        fail_message: null,
        params: {},
        input_handles: {},
        output_handles: null,
        parent_job_id: null,
        shard_element_id: null,
        expected_shards: null,
        shard_element_ids: null,
        started_ts: RUN_STARTED,
        created_ts: RUN_STARTED,
        updated_ts: RUN_UPDATED,
      },
    ]);
    await page.goto(`/w/${WORKFLOW_ID}`);
    const card = page.locator('[data-hololab-node="algorithm"]').first();
    await expect(card).toBeVisible({ timeout: 15_000 });
    await expect(card).toContainText("kiri4090 · v0.4.0");
    // Elapsed = 26s, relative = 12分钟前
    await expect(card).toContainText("26s");
    await expect(card).toContainText("分钟前");
    await expect(card).toContainText("100/100");
    await screenshotCard(page, "test-results/card-done.png");
  });
});
