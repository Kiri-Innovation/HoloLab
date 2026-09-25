// Batched shard progress — the fan-out group row must show element-level
// numbers alongside the coalesced-shard count.
//
// Motivation (2026-09-25): ``GraphNode.batch_size`` lets the gateway
// coalesce N elements into one shard subprocess (see fanout commit
// a9de30c). Before this spec, both the canvas node card and the
// RecentJobsPanel group row still read as "6/13 shards done" — hiding
// the fact that 6 batches represent 48 finished frames. Operators
// running a 100-frame extraction saw a suspiciously slow "1/13" and
// couldn't tell whether the pipeline was healthy without opening
// the log. Element progress restores the domain signal.
//
// Byte-identical contract for batch_size=1: the ``shard_element_ids``
// field is null on non-batched shards, so every rendering branch that
// looks at it should fall back to the pre-batching display exactly.
// The ``batch_size_1`` case guards that regression.

import { test, expect, type Route } from "@playwright/test";

const WORKFLOW_ID = "wf-batched";
const SNAPSHOT_ID = "snap-batched";
const GRAPH_NODE_ID = "fx-1";
const NODE_ID = "node-a";
const PACK_NAME = "frame-extraction";
const PACK_VERSION = "0.3.0";
const CREATED_TS = 1_700_000_000;

function pack() {
  return {
    name: PACK_NAME,
    version: PACK_VERSION,
    manifest_hash: "hash",
    node_ids: [NODE_ID],
    description: "extract frames",
    category: [],
    docs: null,
    source_entry: null,
    manifest_path: null,
    source_dir: null,
    inputs: {},
    outputs: {
      images: {
        tags: ["image"],
        arrayed: true,
        preview: null,
        description: null,
        dim_labels: null,
      },
    },
    params: {},
    arrayable: true,
  };
}

function workflowGraph(batchSize: number) {
  return {
    nodes: [
      {
        id: GRAPH_NODE_ID,
        algorithm_name: PACK_NAME,
        algorithm_version: PACK_VERSION,
        position: { x: 100, y: 100 },
        params: {},
        assigned_node_id: NODE_ID,
        arrayed_toggle: true,
        parallelism: 4,
        batch_size: batchSize,
      },
    ],
    edges: [],
  };
}

// Build a batched fan-out snapshot: 21 elements, batch_size=8 → 3 shards.
// The first shard is done (covers 8 elements), the rest are running/pending.
// Expected panel display: ``×21`` (elements), ``8/21 · 1/3b`` (elems · batches).
function batchedJobsSnapshot() {
  const parent = {
    job_id: "p",
    workflow_id: WORKFLOW_ID,
    node_id: NODE_ID,
    graph_node_id: GRAPH_NODE_ID,
    algorithm_name: PACK_NAME,
    algorithm_version: PACK_VERSION,
    state: "running",
    progress: null,
    fail_reason: null,
    fail_exit_code: null,
    fail_message: null,
    params: {},
    input_handles: {},
    output_handles: null,
    parent_job_id: null,
    shard_element_id: null,
    shard_element_ids: null,
    expected_shards: 3,
    created_ts: CREATED_TS,
    updated_ts: CREATED_TS + 1,
  };
  const s0 = {
    ...parent,
    job_id: "s0",
    parent_job_id: "p",
    shard_element_id: "cam_00",
    shard_element_ids: Array.from({ length: 8 }, (_, k) => `cam_${String(k).padStart(2, "0")}`),
    expected_shards: null,
    state: "done",
    created_ts: CREATED_TS + 2,
    updated_ts: CREATED_TS + 30,
  };
  const s1 = {
    ...parent,
    job_id: "s1",
    parent_job_id: "p",
    shard_element_id: "cam_08",
    shard_element_ids: Array.from({ length: 8 }, (_, k) => `cam_${String(k + 8).padStart(2, "0")}`),
    expected_shards: null,
    state: "running",
    created_ts: CREATED_TS + 3,
    updated_ts: CREATED_TS + 40,
  };
  const s2 = {
    ...parent,
    job_id: "s2",
    parent_job_id: "p",
    shard_element_id: "cam_16",
    shard_element_ids: ["cam_16", "cam_17", "cam_18", "cam_19", "cam_20"],
    expected_shards: null,
    state: "pending",
    created_ts: CREATED_TS + 4,
    updated_ts: CREATED_TS + 4,
  };
  return [parent, s0, s1, s2];
}

// Same domain shape but batch_size=1: 6 elements, 6 shards, first done.
// Expected panel display: ``×6``, ``1/6`` — byte-identical to pre-batching.
function nonBatchedJobsSnapshot() {
  const parent = {
    job_id: "p",
    workflow_id: WORKFLOW_ID,
    node_id: NODE_ID,
    graph_node_id: GRAPH_NODE_ID,
    algorithm_name: PACK_NAME,
    algorithm_version: PACK_VERSION,
    state: "running",
    progress: null,
    fail_reason: null,
    fail_exit_code: null,
    fail_message: null,
    params: {},
    input_handles: {},
    output_handles: null,
    parent_job_id: null,
    shard_element_id: null,
    shard_element_ids: null,
    expected_shards: 6,
    created_ts: CREATED_TS,
    updated_ts: CREATED_TS + 1,
  };
  const shards = Array.from({ length: 6 }, (_, i) => ({
    ...parent,
    job_id: `s${i}`,
    parent_job_id: "p",
    shard_element_id: `cam_${String(i).padStart(2, "0")}`,
    shard_element_ids: null,
    expected_shards: null,
    state: i === 0 ? "done" : "pending",
    created_ts: CREATED_TS + 2 + i,
    updated_ts: CREATED_TS + 30 + i,
  }));
  return [parent, ...shards];
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

// Convert a SnapshotJob-shaped record into a JobSummary the /api/jobs
// endpoint hands out. The RecentJobsPanel reads from this endpoint (not
// from the snapshot fetch), so it must be seeded independently.
function toJobSummary(j: {
  job_id: string;
  workflow_id: string;
  node_id: string | null;
  graph_node_id: string | null;
  algorithm_name: string;
  algorithm_version: string;
  state: string;
  parent_job_id: string | null;
  shard_element_id: string | null;
  shard_element_ids: string[] | null;
  expected_shards: number | null;
  created_ts: number;
  updated_ts: number;
}) {
  return {
    ...j,
    progress: null,
    fail_reason: null,
    started_ts: null,
  };
}

async function stubCommon(page: import("@playwright/test").Page, batchSize: number, jobs: ReturnType<typeof batchedJobsSnapshot>) {
  await page.route("**/api/pack-catalog", jsonRoute([pack()]));
  await page.route("**/api/nodes", jsonRoute([
    {
      node_id: NODE_ID,
      node_name: "node-a",
      gpu: { total: 0, gpus: [] },
      packs: [{ name: PACK_NAME, version: PACK_VERSION, manifest_hash: "hash" }],
      connected_ts: CREATED_TS,
      advertised_url: null,
      workspace_root: "/ws",
      legacy_workspace_roots: [],
      flops_executor_id: null,
      packs_dir: "/packs",
      pack_dirs: ["/packs"],
    },
  ]));
  await page.route("**/api/nodes/metrics/history**", jsonRoute({ sample_interval_s: 5, nodes: {} }));
  await page.route("**/api/jobs", jsonRoute(jobs.map(toJobSummary)));
  await page.route("**/api/artifacts/summary", jsonRoute({ total_bytes: 0, exclusive_bytes: 0, artifact_count: 0 }));
  await page.route("**/api/workflows", jsonRoute([]));
  await page.route(`**/api/workflows/${WORKFLOW_ID}`, jsonRoute({
    workflow_id: WORKFLOW_ID,
    name: "batched",
    graph: workflowGraph(batchSize),
    created_ts: CREATED_TS,
    updated_ts: CREATED_TS,
  }));
  await page.route(`**/api/workflows/${WORKFLOW_ID}/runs`, jsonRoute([
    {
      snapshot_id: SNAPSHOT_ID,
      workflow_id: WORKFLOW_ID,
      created_ts: CREATED_TS,
      node_count: 1,
      job_count: 4,
      state_counts: { running: 1, pending: 1, done: 1, failed: 0 },
      overall_state: "running",
      favorite: false,
      note: null,
    },
  ]));
}

test.describe("Batched shard progress — element counts alongside batches", () => {
  test("batch_size=8: panel shows elements/batches; node card shows element progress", async ({ page }) => {
    const jobs = batchedJobsSnapshot();
    await stubCommon(page, 8, jobs);
    await page.route(`**/api/snapshots/${SNAPSHOT_ID}`, jsonRoute({
      snapshot_id: SNAPSHOT_ID,
      workflow_id: WORKFLOW_ID,
      created_ts: CREATED_TS,
      graph: workflowGraph(8),
      jobs,
    }));

    await page.goto(`/w/${WORKFLOW_ID}`);

    // Panel row: element count ×21 (three shards of 8+8+5). Progress
    // cell reads "8/21 · 1/3b" — elements first (domain progress),
    // batches second (parallelism).
    const groupRow = page.locator('[data-hl-view-log]').first();
    await expect(groupRow).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(/×21/)).toBeVisible();
    await expect(page.getByText(/8\/21\s*·\s*1\/3b/)).toBeVisible();

    // Node card footer: shows element progress (8/21). No batch count
    // there — the card stays terse; batch parallelism lives in the panel.
    const card = page.locator('[data-hololab-node="algorithm"]').first();
    await expect(card).toHaveAttribute("data-state", "running");
    await expect(card).toContainText("8/21");

    await page.screenshot({
      path: "e2e-screenshots/batched-shards-batch8.png",
      fullPage: true,
    });
  });

  test("batch_size=1: display is byte-identical to pre-batching (X/N only)", async ({ page }) => {
    const jobs = nonBatchedJobsSnapshot();
    await stubCommon(page, 1, jobs);
    await page.route(`**/api/snapshots/${SNAPSHOT_ID}`, jsonRoute({
      snapshot_id: SNAPSHOT_ID,
      workflow_id: WORKFLOW_ID,
      created_ts: CREATED_TS,
      graph: workflowGraph(1),
      jobs,
    }));

    await page.goto(`/w/${WORKFLOW_ID}`);

    // No batch suffix — plain "1/6" as before batching existed. The
    // "b" suffix is the tell for a batched run; its absence here is
    // the byte-identical guard.
    await expect(page.getByText(/×6/)).toBeVisible({ timeout: 15_000 });
    const progressCell = page.getByText(/^1\/6$/).first();
    await expect(progressCell).toBeVisible();
    await expect(page.getByText(/\/6b/)).toHaveCount(0);

    const card = page.locator('[data-hololab-node="algorithm"]').first();
    await expect(card).toContainText("1/6");

    await page.screenshot({
      path: "e2e-screenshots/batched-shards-batch1.png",
      fullPage: true,
    });
  });
});
