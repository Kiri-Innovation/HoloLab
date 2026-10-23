// Regression for "operator can't tell the running job is using OLD params".
//
// Real incident (2026-09-21): a triangulate node was configured
// ``use_gpu=1`` in the draft, but the still-running shard command line
// showed ``--use-gpu 0``. The operator opened a bug against param
// passing. Not a bug: the run had been dispatched at 17:25 with
// ``use_gpu=0``, the draft was edited to ``use_gpu=1`` at 17:31, and
// the UI gave no signal that the two had diverged — canvas/staleness.ts
// deliberately suppressed the amber dot while a job was in-flight, on
// the theory that the status dot alone told the whole story.
//
// The fix adds a third staleness kind ``inflight_old_params`` that
// renders a compact ``!旧参数`` chip on the node header whenever a
// running/pending/assigned job's frozen ``params`` diverge from the
// current draft. The old amber self_dirty dot is unchanged for the
// "edited but nothing in flight" case (quieter tone), so the two states
// stay visually distinct.
//
// This suite stubs the full REST surface and drives three fixtures:
//   1. running + drift → chip visible on node A, absent on node B.
//   2. done + drift → amber self_dirty dot visible, chip absent.
//   3. done + no drift → clean node, no chip, no dot.

import { test, expect, type Route } from "@playwright/test";

const WORKFLOW_ID = "wf-inflight-drift";
const SNAPSHOT_ID = "snap-inflight-drift";
const NODE_ID = "node-a";
const PACK_NAME = "colmap-triangulate";
const PACK_VERSION = "0.4.0";
const CREATED_TS = 1_700_000_000;

// Two graph-node ids used by the "running with drift, running without
// drift" mixed fixture. A drifts, B does not, so a single screenshot
// captures the visual contrast.
const GNID_DRIFT = "colmap-triangulate-drift";
const GNID_MATCH = "colmap-triangulate-match";

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
    // A single boolean-ish param the fixtures flip to force drift.
    params: { use_gpu: { type: "int", default: 0, description: "1=gpu" } },
    arrayable: true,
  };
}

// Draft graph: the ``params`` here are what the operator sees in the
// canvas + config drawer. Any divergence from the SnapshotJob.params
// below is what the new chip surfaces.
function draftGraph(driftParams: Record<string, unknown>, matchParams: Record<string, unknown>) {
  return {
    nodes: [
      {
        id: GNID_DRIFT,
        algorithm_name: PACK_NAME,
        algorithm_version: PACK_VERSION,
        position: { x: 100, y: 100 },
        params: driftParams,
        assigned_node_id: NODE_ID,
        arrayed_toggle: false,
        parallelism: 1,
      },
      {
        id: GNID_MATCH,
        algorithm_name: PACK_NAME,
        algorithm_version: PACK_VERSION,
        position: { x: 100, y: 260 },
        params: matchParams,
        assigned_node_id: NODE_ID,
        arrayed_toggle: false,
        parallelism: 1,
      },
    ],
    edges: [],
  };
}

// The graph that landed in the snapshot at dispatch time — separate
// from the draft above so we can force a drift.
function snapshotGraph(dispatchedParams: Record<string, unknown>) {
  return {
    nodes: [
      {
        id: GNID_DRIFT,
        algorithm_name: PACK_NAME,
        algorithm_version: PACK_VERSION,
        position: { x: 100, y: 100 },
        params: dispatchedParams,
        assigned_node_id: NODE_ID,
        arrayed_toggle: false,
        parallelism: 1,
      },
      {
        id: GNID_MATCH,
        algorithm_name: PACK_NAME,
        algorithm_version: PACK_VERSION,
        position: { x: 100, y: 260 },
        params: dispatchedParams,
        assigned_node_id: NODE_ID,
        arrayed_toggle: false,
        parallelism: 1,
      },
    ],
    edges: [],
  };
}

interface JobOpts {
  gnid: string;
  state: string;
  params: Record<string, unknown>;
  job_id: string;
}

function job(opts: JobOpts) {
  return {
    job_id: opts.job_id,
    workflow_id: WORKFLOW_ID,
    node_id: NODE_ID,
    graph_node_id: opts.gnid,
    algorithm_name: PACK_NAME,
    algorithm_version: PACK_VERSION,
    state: opts.state,
    progress: null,
    fail_reason: null,
    fail_exit_code: null,
    fail_message: null,
    params: opts.params,
    input_handles: {},
    output_handles: null,
    parent_job_id: null,
    shard_element_id: null,
    expected_shards: null,
    created_ts: CREATED_TS,
    updated_ts: CREATED_TS,
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

async function stubCommon(page: import("@playwright/test").Page, kind: 'inflight_old_params' | 'self_dirty' | null) {
  await page.routeWebSocket('**', () => {});
  // Gateway owns drift; the browser must not infer it from sparse historical jobs.
  await page.route(`**/api/workflows/${WORKFLOW_ID}/analyze`, async route => {
    const graph=route.request().postDataJSON().graph;
    await route.fulfill({json:{revision:'test',graph,diff:[],environment_params:{},staleness:{
      [GNID_DRIFT]:kind ? {kind,title:kind === 'inflight_old_params' ? '运行中的任务使用已冻结的旧配置' : '有效配置与上次运行不同',changeReasons:['use_gpu 已变化']} : null,
      [GNID_MATCH]:null,
    }}});
  });
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
  await page.route(`**/api/workflows/${WORKFLOW_ID}/runs`, jsonRoute([
    {
      snapshot_id: SNAPSHOT_ID,
      workflow_id: WORKFLOW_ID,
      created_ts: CREATED_TS,
      node_count: 2,
      job_count: 2,
      state_counts: { running: 2 },
      overall_state: "running",
      favorite: false,
      note: null,
    },
  ]));
}

test.describe("Canvas node — inflight_old_params chip", () => {
  test("running + drifted draft → single warning dot with old-parameter tooltip", async ({
    page,
  }) => {
    // Draft: drift node has use_gpu=1, match node has use_gpu=0.
    // Snapshot + running jobs: both use_gpu=0. Only the drift node
    // should surface the chip.
    const draft = draftGraph({ use_gpu: 1 }, { use_gpu: 0 });
    const snap = snapshotGraph({ use_gpu: 0 });

    await stubCommon(page, 'inflight_old_params');
    await page.route(`**/api/workflows/${WORKFLOW_ID}`, jsonRoute({
      workflow_id: WORKFLOW_ID,
      name: "inflight-drift",
      graph: draft,
      created_ts: CREATED_TS,
      updated_ts: CREATED_TS,
    }));
    await page.route(`**/api/snapshots/${SNAPSHOT_ID}`, jsonRoute({
      snapshot_id: SNAPSHOT_ID,
      workflow_id: WORKFLOW_ID,
      created_ts: CREATED_TS,
      graph: snap,
      jobs: [
        job({ gnid: GNID_DRIFT, state: "running", params: { use_gpu: 0 }, job_id: "job-drift-01" }),
        job({ gnid: GNID_MATCH, state: "running", params: { use_gpu: 0 }, job_id: "job-match-01" }),
      ],
    }));

    await page.goto(`/w/${WORKFLOW_ID}`);

    // Both node cards should be present and both status dots should
    // read ``running`` — that's the precondition the chip modifies.
    const dots = page.locator(`[data-hl-node-status]`);
    await expect(dots).toHaveCount(2, { timeout: 15_000 });
    await expect(dots.first()).toHaveAttribute("data-hl-node-status", "running");
    await expect(dots.nth(1)).toHaveAttribute("data-hl-node-status", "running");

    // Drift changes the single status dot; raw running state is retained.
    await expect(dots.first()).toHaveAttribute("data-status-tone", "warning");
    await expect(dots.nth(1)).toHaveAttribute("data-status-tone", "info");
    await expect(page.locator('[data-hl-node-stale]')).toHaveCount(0);
    await dots.first().hover();
    const tooltip=page.getByRole('tooltip');
    await expect(tooltip).toBeVisible();
    await expect(tooltip).toContainText('旧参数');
    await expect(tooltip).toContainText('use_gpu');
    await expect(dots.first()).not.toHaveAttribute('title');

    await page.screenshot({
      path: "test-results/inflight-drift-chip-visible.png",
      fullPage: true,
    });
  });

  test("done + drifted draft → single warning dot", async ({
    page,
  }) => {
    // Same drift, but the jobs are DONE — the operator has finished
    // running and then edited the draft. This is the pre-existing
    // ``self_dirty`` case and its quiet amber dot must still be the
    // rendering (the chip is louder and reserved for in-flight drift).
    const draft = draftGraph({ use_gpu: 1 }, { use_gpu: 0 });
    const snap = snapshotGraph({ use_gpu: 0 });

    await stubCommon(page, 'self_dirty');
    await page.route(`**/api/workflows/${WORKFLOW_ID}`, jsonRoute({
      workflow_id: WORKFLOW_ID,
      name: "done-drift",
      graph: draft,
      created_ts: CREATED_TS,
      updated_ts: CREATED_TS,
    }));
    await page.route(`**/api/snapshots/${SNAPSHOT_ID}`, jsonRoute({
      snapshot_id: SNAPSHOT_ID,
      workflow_id: WORKFLOW_ID,
      created_ts: CREATED_TS,
      graph: snap,
      jobs: [
        job({ gnid: GNID_DRIFT, state: "done", params: { use_gpu: 0 }, job_id: "job-drift-02" }),
        job({ gnid: GNID_MATCH, state: "done", params: { use_gpu: 0 }, job_id: "job-match-02" }),
      ],
    }));

    await page.goto(`/w/${WORKFLOW_ID}`);

    const dots = page.locator(`[data-hl-node-status]`);
    await expect(dots).toHaveCount(2, { timeout: 15_000 });
    await expect(dots.first()).toHaveAttribute("data-hl-node-status", "done");

    // Chip absent — it is EXCLUSIVELY the in-flight-drift indicator.
    await expect(page.locator(`[data-hl-node-stale="inflight_old_params"]`)).toHaveCount(0);
    // Amber self_dirty dot present on the drifted node.
    const stale = page.locator(`[data-hl-node-status][data-status-tone="warning"]`);
    await expect(stale).toHaveCount(1);

    await page.screenshot({
      path: "test-results/inflight-drift-done-self-dirty.png",
      fullPage: true,
    });
  });

  test("no drift → no chip, no dot (post-rerun clean state)", async ({
    page,
  }) => {
    // The workflow that would land after the operator sees the chip
    // and reruns: draft matches snapshot matches job.params. Everything
    // must be quiet — this is the ambient "no news is good news" state.
    const draft = draftGraph({ use_gpu: 1 }, { use_gpu: 1 });
    const snap = snapshotGraph({ use_gpu: 1 });

    await stubCommon(page, null);
    await page.route(`**/api/workflows/${WORKFLOW_ID}`, jsonRoute({
      workflow_id: WORKFLOW_ID,
      name: "no-drift",
      graph: draft,
      created_ts: CREATED_TS,
      updated_ts: CREATED_TS,
    }));
    await page.route(`**/api/snapshots/${SNAPSHOT_ID}`, jsonRoute({
      snapshot_id: SNAPSHOT_ID,
      workflow_id: WORKFLOW_ID,
      created_ts: CREATED_TS,
      graph: snap,
      jobs: [
        job({ gnid: GNID_DRIFT, state: "done", params: { use_gpu: 1 }, job_id: "job-clean-01" }),
        job({ gnid: GNID_MATCH, state: "done", params: { use_gpu: 1 }, job_id: "job-clean-02" }),
      ],
    }));

    await page.goto(`/w/${WORKFLOW_ID}`);

    const dots = page.locator(`[data-hl-node-status]`);
    await expect(dots).toHaveCount(2, { timeout: 15_000 });
    await expect(page.locator(`[data-hl-node-stale]`)).toHaveCount(0);

    await page.screenshot({
      path: "test-results/inflight-drift-clean.png",
      fullPage: true,
    });
  });
});
