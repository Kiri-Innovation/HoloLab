// Regression for the collapsed-node-card "跳转产物位置" footer button.
//
// User request (2026-09-23): give the algorithm node's collapsed footer
// a direct-jump affordance to the on-disk artifact location. Contract:
//
//   * button appears ONLY when the node has at least one resolved
//     preview target (i.e. it has produced artifacts at least once);
//   * button appears ONLY in the collapsed footer — expanding the
//     preview drawer surfaces per-port Open-in-Cocoder buttons that
//     already cover the same intent;
//   * button hides in non-Flops-Cobrowser environments (regular
//     browsers) because ``window.flops.showDocument`` is the transport.
//
// The suite stubs the whole REST surface + a fake ``window.flops`` so
// we can assert the footer selector state without hitting a real
// gateway or a real Cobrowser host.

import { test, expect, type Route } from "@playwright/test";

const WORKFLOW_ID = "wf-artifact-btn";
const SNAPSHOT_ID = "snap-artifact-btn";
const GRAPH_NODE_ID_WITH_ARTIFACTS = "sfm-done";
const GRAPH_NODE_ID_NO_ARTIFACTS = "sfm-fresh";
const NODE_ID = "compute-a";
const PACK_NAME = "colmap-sfm";
const PACK_VERSION = "0.4.0";
const CREATED_TS = 1_700_000_000;
const ARTIFACT_PATH = "/data/runs/sfm-done/frames.dir";
const HANDLE_ID = "handle-frames-1";

function pack() {
  return {
    name: PACK_NAME,
    version: PACK_VERSION,
    manifest_hash: "hash",
    node_ids: [NODE_ID],
    description: "sfm test pack",
    category: [],
    docs: null,
    source_entry: null,
    manifest_path: null,
    source_dir: null,
    inputs: {},
    outputs: {
      frames: {
        tags: ["image_sequence"],
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

function workflowGraph() {
  return {
    nodes: [
      {
        id: GRAPH_NODE_ID_WITH_ARTIFACTS,
        algorithm_name: PACK_NAME,
        algorithm_version: PACK_VERSION,
        position: { x: 100, y: 100 },
        params: {},
        assigned_node_id: NODE_ID,
      },
      {
        id: GRAPH_NODE_ID_NO_ARTIFACTS,
        algorithm_name: PACK_NAME,
        algorithm_version: PACK_VERSION,
        position: { x: 100, y: 320 },
        params: {},
        assigned_node_id: NODE_ID,
      },
    ],
    edges: [],
  };
}

function doneJobWithHandle() {
  return {
    job_id: "job-done",
    workflow_id: WORKFLOW_ID,
    node_id: NODE_ID,
    graph_node_id: GRAPH_NODE_ID_WITH_ARTIFACTS,
    algorithm_name: PACK_NAME,
    algorithm_version: PACK_VERSION,
    state: "done",
    progress: null,
    fail_reason: null,
    fail_exit_code: null,
    fail_message: null,
    params: {},
    input_handles: {},
    output_handles: { frames: HANDLE_ID },
    parent_job_id: null,
    shard_element_id: null,
    expected_shards: null,
    created_ts: CREATED_TS,
    updated_ts: CREATED_TS + 10,
  };
}

function handleInfo() {
  return {
    handle_id: HANDLE_ID,
    node_id: NODE_ID,
    storage: "dir",
    tags: ["image_sequence"],
    size_bytes: 0,
    output_port_name: "frames",
    proxy_url: `/proxy/${HANDLE_ID}/`,
    absolute_path: ARTIFACT_PATH,
    deleted_ts: null,
    preview: null,
    dim_labels: null,
    dim_sizes: null,
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
  await page.route(
    "**/api/nodes",
    jsonRoute([
      {
        node_id: NODE_ID,
        node_name: "compute-a",
        gpu: { total: 0, gpus: [] },
        packs: [{ name: PACK_NAME, version: PACK_VERSION, manifest_hash: "hash" }],
        connected_ts: CREATED_TS,
        advertised_url: null,
        workspace_root: "/ws",
        legacy_workspace_roots: [],
        flops_executor_id: "device-abc",
        packs_dir: "/packs",
        pack_dirs: ["/packs"],
      },
    ]),
  );
  await page.route(
    "**/api/nodes/metrics/history**",
    jsonRoute({ sample_interval_s: 5, nodes: {} }),
  );
  await page.route("**/api/jobs", jsonRoute([]));
  await page.route(
    "**/api/artifacts/summary",
    jsonRoute({ total_bytes: 0, exclusive_bytes: 0, artifact_count: 0 }),
  );
  await page.route("**/api/workflows", jsonRoute([]));
  await page.route(
    `**/api/workflows/${WORKFLOW_ID}`,
    jsonRoute({
      workflow_id: WORKFLOW_ID,
      name: "artifact-btn",
      graph: workflowGraph(),
      created_ts: CREATED_TS,
      updated_ts: CREATED_TS,
    }),
  );
  await page.route(
    `**/api/workflows/${WORKFLOW_ID}/runs`,
    jsonRoute([
      {
        snapshot_id: SNAPSHOT_ID,
        workflow_id: WORKFLOW_ID,
        created_ts: CREATED_TS,
        node_count: 2,
        job_count: 1,
        state_counts: { done: 1 },
        overall_state: "done",
        favorite: false,
        note: null,
      },
    ]),
  );
  await page.route(
    `**/api/snapshots/${SNAPSHOT_ID}`,
    jsonRoute({
      snapshot_id: SNAPSHOT_ID,
      workflow_id: WORKFLOW_ID,
      created_ts: CREATED_TS,
      graph: workflowGraph(),
      jobs: [doneJobWithHandle()],
    }),
  );
  await page.route(`**/api/handles/${HANDLE_ID}`, jsonRoute(handleInfo()));
  // Frontend also GETs the summary endpoint for edges; return empty.
  await page.route(
    `**/api/handles/${HANDLE_ID}/summary`,
    jsonRoute({ handle_id: HANDLE_ID, storage: "dir", entries: [] }),
  );
}

async function injectFlops(page: import("@playwright/test").Page) {
  // Fake Cobrowser env so the button's ``flopsAvailable()`` gate opens.
  await page.addInitScript(() => {
    (window as unknown as { flops: unknown }).flops = {
      version: 1,
      showDocument: async () => ({ success: true, kind: "dir" }),
    };
  });
}

test.describe("Collapsed footer — 跳转产物位置 button", () => {
  test("with resolved previews + Flops → button visible on the done node only", async ({
    page,
  }) => {
    await injectFlops(page);
    await stubCommon(page);

    await page.goto(`/w/${WORKFLOW_ID}`);

    const doneCard = page.locator(
      `[data-hololab-node="algorithm"][data-state="done"]`,
    );
    await expect(doneCard).toBeVisible({ timeout: 15_000 });

    // Button lives in the collapsed footer of the done card.
    const doneButton = doneCard.locator("[data-hl-open-artifact]");
    await expect(doneButton).toBeVisible({ timeout: 15_000 });
    await expect(doneButton).toHaveAttribute("data-hl-configured", "1");
    await expect(doneButton).toHaveAttribute(
      "title",
      /跳转产物位置.*\/data\/runs\/sfm-done\/frames\.dir/,
    );
    // Screenshot the collapsed card so the visual result is auditable
    // from the Playwright report.
    await doneCard.screenshot({
      path: "test-results/artifact-btn-collapsed.png",
    });

    // The never-ran node has no previews → button must be absent.
    const freshCards = page.locator(
      `[data-hololab-node="algorithm"]:not([data-state="done"])`,
    );
    await expect(freshCards.first()).toBeVisible();
    await expect(freshCards.first().locator("[data-hl-open-artifact]")).toHaveCount(0);
  });

  test("expanded drawer → button hides (per-port drawer buttons take over)", async ({
    page,
  }) => {
    await injectFlops(page);
    await stubCommon(page);

    await page.goto(`/w/${WORKFLOW_ID}`);

    const doneCard = page.locator(
      `[data-hololab-node="algorithm"][data-state="done"]`,
    );
    await expect(doneCard).toBeVisible({ timeout: 15_000 });
    await expect(doneCard.locator("[data-hl-open-artifact]")).toBeVisible();

    // Click the preview caret to expand the drawer.
    const caret = doneCard.locator('button[title="expand preview"]');
    await expect(caret).toBeVisible();
    await caret.click();

    await expect(doneCard).toHaveAttribute("data-preview-open", /.+/);
    // Footer button should disappear once drawer is open.
    await expect(doneCard.locator("[data-hl-open-artifact]")).toHaveCount(0);
  });

  test("without Flops → button never renders even with artifacts", async ({
    page,
  }) => {
    // Explicitly do NOT inject window.flops.
    await stubCommon(page);
    await page.goto(`/w/${WORKFLOW_ID}`);

    const doneCard = page.locator(
      `[data-hololab-node="algorithm"][data-state="done"]`,
    );
    await expect(doneCard).toBeVisible({ timeout: 15_000 });
    await expect(doneCard.locator("[data-hl-open-artifact]")).toHaveCount(0);
  });
});
