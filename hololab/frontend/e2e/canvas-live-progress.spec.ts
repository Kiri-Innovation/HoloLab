// Regression for "canvas node shows red while new run is still ticking".
//
// Scenario the user hit (2026-09-21): they kicked off a fresh fan-out
// on colmap-triangulate. Two shards had already finished (``2/100``) and
// many more were running/pending, but a couple of early shards had
// failed. The node's border stayed red because ``aggregateJobsToRuntime``
// used a ``hasFailed → return 'failed'`` short-circuit that ignored the
// still-in-flight jobs. Operators watching a live dispatch read the red
// border as "run is dead" and reached for the reload key.
//
// The fix inverts the priority: any in-flight (running/assigned/pending)
// job pushes the aggregate to ``running`` even when a shard has failed.
// The failed-shard tooltip is still surfaced via ``fail_reason``.
//
// This suite stubs the whole REST surface so it can exercise the canvas
// without a live gateway. The critical assertion is the status dot's
// ``data-hl-node-status`` attribute, which reads directly off the
// aggregator's output — asserting on it is asserting on the reducer.

import { test, expect, type Route } from "@playwright/test";

const WORKFLOW_ID = "wf-live-progress";
const SNAPSHOT_ID = "snap-mixed";
const GRAPH_NODE_ID = "colmap-triangulate-1";
const NODE_ID = "node-a";
const PACK_NAME = "colmap-triangulate";
const PACK_VERSION = "0.4.0";
const CREATED_TS = 1_700_000_000;

function pack() {
  return {
    name: PACK_NAME,
    version: PACK_VERSION,
    manifest_hash: "hash",
    node_ids: [NODE_ID],
    description: "triangulate",
    category: [],
    docs: null,
    source_entry: null,
    manifest_path: null,
    source_dir: null,
    inputs: {},
    outputs: {
      colmap: {
        tags: ["colmap"],
        arrayed: false,
        preview: null,
        description: null,
        dim_labels: null,
      },
    },
    params: {},
    arrayable: true,
  };
}

function workflowGraph() {
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
      },
    ],
    edges: [],
  };
}

// The core scenario: a live fan-out with a mix of states. 1 shard done,
// 3 shards running/pending, and 1 shard failed. Under the old priority
// this rendered red; under the new priority it must render "running".
function mixedJobsSnapshot() {
  const parent = {
    job_id: "parent",
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
    expected_shards: 100,
    created_ts: CREATED_TS,
    updated_ts: CREATED_TS + 1,
  };
  const shardDone = {
    ...parent,
    job_id: "shard-done",
    parent_job_id: "parent",
    shard_element_id: "0000",
    expected_shards: null,
    state: "done",
    created_ts: CREATED_TS + 2,
    updated_ts: CREATED_TS + 30,
  };
  const shardFailed = {
    ...parent,
    job_id: "shard-failed",
    parent_job_id: "parent",
    shard_element_id: "0001",
    expected_shards: null,
    state: "failed",
    fail_reason: "shard 1 blew up early",
    created_ts: CREATED_TS + 3,
    updated_ts: CREATED_TS + 20,
  };
  const shardRunning = {
    ...parent,
    job_id: "shard-running",
    parent_job_id: "parent",
    shard_element_id: "0002",
    expected_shards: null,
    state: "running",
    created_ts: CREATED_TS + 4,
    updated_ts: CREATED_TS + 40,
  };
  const shardPending = {
    ...parent,
    job_id: "shard-pending",
    parent_job_id: "parent",
    shard_element_id: "0003",
    expected_shards: null,
    state: "pending",
    created_ts: CREATED_TS + 5,
    updated_ts: CREATED_TS + 5,
  };
  return [parent, shardDone, shardFailed, shardRunning, shardPending];
}

// Same shape but with every job terminal: 1 done + 1 failed, nothing in
// flight. This must resolve to ``failed`` — the terminal branch of the
// priority. Guards against a naive "always running" regression.
function terminatedJobsSnapshot() {
  const parent = {
    job_id: "term-parent",
    workflow_id: WORKFLOW_ID,
    node_id: NODE_ID,
    graph_node_id: GRAPH_NODE_ID,
    algorithm_name: PACK_NAME,
    algorithm_version: PACK_VERSION,
    state: "failed",
    progress: null,
    fail_reason: "shard 1 blew up early",
    fail_exit_code: null,
    fail_message: null,
    params: {},
    input_handles: {},
    output_handles: null,
    parent_job_id: null,
    shard_element_id: null,
    expected_shards: 2,
    created_ts: CREATED_TS,
    updated_ts: CREATED_TS + 60,
  };
  const done = { ...parent, job_id: "term-done", parent_job_id: "term-parent", shard_element_id: "0000", expected_shards: null, state: "done", fail_reason: null, created_ts: CREATED_TS + 1, updated_ts: CREATED_TS + 30 };
  const failed = { ...parent, job_id: "term-failed", parent_job_id: "term-parent", shard_element_id: "0001", expected_shards: null, state: "failed", fail_reason: "shard 1 blew up early", created_ts: CREATED_TS + 2, updated_ts: CREATED_TS + 40 };
  return [parent, done, failed];
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
  await page.route("**/api/jobs", jsonRoute([]));
  await page.route("**/api/artifacts/summary", jsonRoute({ total_bytes: 0, exclusive_bytes: 0, artifact_count: 0 }));
  await page.route("**/api/workflows", jsonRoute([]));
  await page.route(`**/api/workflows/${WORKFLOW_ID}`, jsonRoute({
    workflow_id: WORKFLOW_ID,
    name: "live-progress",
    graph: workflowGraph(),
    created_ts: CREATED_TS,
    updated_ts: CREATED_TS,
  }));
  await page.route(`**/api/workflows/${WORKFLOW_ID}/runs`, jsonRoute([
    {
      snapshot_id: SNAPSHOT_ID,
      workflow_id: WORKFLOW_ID,
      created_ts: CREATED_TS,
      node_count: 1,
      job_count: 5,
      state_counts: { running: 2, pending: 1, done: 1, failed: 1 },
      overall_state: "running",
      favorite: false,
      note: null,
    },
  ]));
}

test.describe("Canvas node status — live progress trumps stale failure", () => {
  test("mixed in-flight + failed shards → node dot reads 'running', not 'failed'", async ({
    page,
  }) => {
    await stubCommon(page);
    await page.route(`**/api/snapshots/${SNAPSHOT_ID}`, jsonRoute({
      snapshot_id: SNAPSHOT_ID,
      workflow_id: WORKFLOW_ID,
      created_ts: CREATED_TS,
      graph: workflowGraph(),
      jobs: mixedJobsSnapshot(),
    }));

    await page.goto(`/w/${WORKFLOW_ID}`);

    // The status dot lives on the AlgorithmNode header — its
    // ``data-hl-node-status`` attribute is set from the aggregated
    // ``runState`` (see canvas/AlgorithmNode.tsx). Poll the DOM so we
    // read it after hydration finishes rather than mid-cold-load.
    const dot = page.locator(`[data-hl-node-status]`).first();
    await expect(dot).toHaveAttribute("data-hl-node-status", "running", {
      timeout: 15_000,
    });
    // The card frame also carries data-state — checked here as a
    // second-source-of-truth so a future refactor that decouples the
    // dot from the card doesn't silently drift.
    const card = page.locator(`[data-hololab-node="algorithm"]`).first();
    await expect(card).toHaveAttribute("data-state", "running");
  });

  test("all terminal + at least one failed → node dot reads 'failed'", async ({
    page,
  }) => {
    await stubCommon(page);
    await page.route(`**/api/snapshots/${SNAPSHOT_ID}`, jsonRoute({
      snapshot_id: SNAPSHOT_ID,
      workflow_id: WORKFLOW_ID,
      created_ts: CREATED_TS,
      graph: workflowGraph(),
      jobs: terminatedJobsSnapshot(),
    }));

    await page.goto(`/w/${WORKFLOW_ID}`);
    const dot = page.locator(`[data-hl-node-status]`).first();
    await expect(dot).toHaveAttribute("data-hl-node-status", "failed", {
      timeout: 15_000,
    });
  });
});
