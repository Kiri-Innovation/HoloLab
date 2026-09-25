// Regression for the "Agent 与 UI 能力须对称" invariant.
//
// The gateway's ``GraphNode.batch_size`` field is settable via REST but
// had no NodeInspector control — an operator could only inspect it by
// tailing SQL. This spec pins the new number input: it renders next to
// ``parallelism`` when the pack is arrayable and ``arrayed_toggle`` is
// on, edits mutate the controlled value (proving App-level plumbing
// wires the patch through toGraph), and the field stays hidden when
// arrayed_toggle is off (batch_size is only meaningful on fan-outs —
// mirroring the parallelism gate).
//
// Autosave round-trip is not re-asserted here — e2e/network-resilience
// already exercises that path end-to-end via the same useDraftAutosave
// hook; duplicating the wait+poll for POST /api/workflows would only
// add flake without new coverage.

import { test, expect, type Route } from "@playwright/test";

const WORKFLOW_ID = "wf-batch-size";
const NODE_ID = "node-a";
const PACK_NAME = "colmap-triangulate";
const PACK_VERSION = "0.4.0";
const GNID = "gnode-arrayed";
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

function draftGraph(over: { arrayed_toggle: boolean; batch_size?: number }) {
  return {
    nodes: [
      {
        id: GNID,
        algorithm_name: PACK_NAME,
        algorithm_version: PACK_VERSION,
        position: { x: 100, y: 100 },
        params: {},
        assigned_node_id: NODE_ID,
        arrayed_toggle: over.arrayed_toggle,
        parallelism: 1,
        batch_size: over.batch_size ?? 1,
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
  await page.route(`**/api/workflows/${WORKFLOW_ID}/runs`, jsonRoute([]));
  // The workflows-index list route (GET), and the autosave POST target
  // (same URL, different method). Absorb both with a permissive handler
  // so autosave doesn't error out during the test.
  await page.route(/\/api\/workflows$/, async (route) => {
    const req = route.request();
    if (req.method() === "POST") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          workflow_id: WORKFLOW_ID,
          name: "batch-size-spec",
          updated_ts: CREATED_TS + 1,
        }),
      });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
}

test.describe("NodeInspector — batch_size", () => {
  test("arrayed_toggle on → input renders, defaults to persisted value, edits accepted", async ({ page }) => {
    await stubCommon(page);
    await page.route(`**/api/workflows/${WORKFLOW_ID}`, jsonRoute({
      workflow_id: WORKFLOW_ID,
      name: "batch-size-spec",
      graph: draftGraph({ arrayed_toggle: true, batch_size: 1 }),
      created_ts: CREATED_TS,
      updated_ts: CREATED_TS,
    }));

    await page.goto(`/w/${WORKFLOW_ID}`);

    // Click the node to select it → NodeInspector renders.
    const node = page.locator(`[data-hololab-node="algorithm"]`);
    await expect(node).toHaveCount(1, { timeout: 15_000 });
    await node.click();

    // Input is present next to parallelism, defaults to the persisted
    // value (1). The min attribute pins the lower bound to ``ge=1`` from
    // the backend GraphNode schema.
    const input = page.locator(`input[data-hl-batch-size]`);
    await expect(input).toBeVisible();
    await expect(input).toHaveValue("1");
    await expect(input).toHaveAttribute("min", "1");

    // Edit → controlled value flips to 4. Only possible if
    // onChange → onInspectorChange → setNodes(batch_size:4) → the
    // Inspector re-reads selected.batch_size = 4 → the input re-renders
    // at 4. Proves the App-level plumbing carries the patch (toGraph
    // and back).
    await input.focus();
    await input.fill("4");
    await input.blur();
    await expect(input).toHaveValue("4");

    // Sub-1 input clamps back to 1 (mirrors the backend ge=1 invariant).
    await input.fill("0");
    await input.blur();
    await expect(input).toHaveValue("1");

    // Screenshot for the record — mirrors the parallelism e2e coverage
    // style so an operator PR reviewer can eyeball the new field.
    await page.screenshot({
      path: "test-results/inspector-batch-size-visible.png",
      fullPage: true,
    });
  });

  test("arrayed_toggle off → input absent (parity with parallelism gate)", async ({ page }) => {
    // batch_size only makes sense on fan-outs; the Inspector must not
    // dangle a bare number field for scalar nodes. Same gate as
    // parallelism at NodeInspector.tsx:298.
    await stubCommon(page);
    await page.route(`**/api/workflows/${WORKFLOW_ID}`, jsonRoute({
      workflow_id: WORKFLOW_ID,
      name: "batch-size-scalar",
      graph: draftGraph({ arrayed_toggle: false }),
      created_ts: CREATED_TS,
      updated_ts: CREATED_TS,
    }));

    await page.goto(`/w/${WORKFLOW_ID}`);

    const node = page.locator(`[data-hololab-node="algorithm"]`);
    await expect(node).toHaveCount(1, { timeout: 15_000 });
    await node.click();

    // Sanity: Fan-out section header exists (the pack is arrayable) but
    // the batch_size and parallelism fields are gated behind the toggle.
    await expect(page.locator(`input[data-hl-arrayed-toggle]`)).toBeVisible();
    await expect(page.locator(`input[data-hl-batch-size]`)).toHaveCount(0);
    await expect(page.locator(`input[data-hl-parallelism]`)).toHaveCount(0);
  });
});
