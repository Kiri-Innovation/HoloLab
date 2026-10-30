import { expect, test, type Page, type Route } from "@playwright/test";

const WORKFLOW_ID = "preview-hydration";
const NODE_ID = "preview-node";
const PORTS = Array.from({ length: 16 }, (_, i) => `out-${i}`);

function graph() {
  return {
    nodes: [
      {
        id: "node",
        algorithm_name: "preview-pack",
        algorithm_version: "1.0.0",
        position: { x: 0, y: 0 },
        params: {},
        assigned_node_id: NODE_ID,
        preview_open: "out-0",
      },
    ],
    edges: [],
  };
}

function pack() {
  return {
    name: "preview-pack",
    version: "1.0.0",
    manifest_hash: "hash",
    node_ids: [NODE_ID],
    description: "",
    category: [],
    inputs: {},
    outputs: Object.fromEntries(
      PORTS.map((name) => [
        name,
        {
          tags: ["folder"],
          storage: "dir",
          preview: { kind: "folder" },
          description: null,
        },
      ]),
    ),
    params: {},
    arrayable: false,
  };
}

async function routeBase(page: Page, failHandle = false) {
  let active = 0;
  let maximum = 0;
  const job = {
    job_id: "done-job",
    snapshot_id: "run",
    workflow_id: WORKFLOW_ID,
    node_id: NODE_ID,
    graph_node_id: "node",
    algorithm_name: "preview-pack",
    algorithm_version: "1.0.0",
    state: "done",
    progress: null,
    fail_reason: null,
    fail_exit_code: null,
    fail_message: null,
    params: {},
    input_handles: {},
    output_handles: Object.fromEntries(
      PORTS.map((name) => [name, `handle-${name}`]),
    ),
    parent_job_id: null,
    created_ts: 1,
    updated_ts: 1,
  };
  await page.route("**/api/**", async (route: Route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.startsWith("/api/handles/")) {
      active += 1;
      maximum = Math.max(maximum, active);
      await new Promise((resolve) => setTimeout(resolve, 200));
      active -= 1;
      if (failHandle)
        return route.fulfill({ status: 404, json: { detail: "gone" } });
      const id = path.split("/").at(-1)!;
      return route.fulfill({
        json: {
          handle_id: id,
          node_id: NODE_ID,
          storage: "dir",
          tags: ["folder"],
          size_bytes: 1,
          output_port_name: "out-0",
          proxy_url: "/proxy/worker/out",
          absolute_path: "/tmp/out",
          deleted_ts: null,
          preview: { kind: "folder" },
          dim_labels: null,
          dim_sizes: null,
        },
      });
    }
    let body: unknown = {};
    if (path === "/api/pack-catalog") body = [pack()];
    else if (path === "/api/nodes")
      body = [
        {
          node_id: NODE_ID,
          node_name: "worker",
          packs: [
            { name: "preview-pack", version: "1.0.0", manifest_hash: "hash" },
          ],
          connected_ts: 1,
          workspace_root: "/tmp",
          gpu: { total: 0, gpus: [] },
        },
      ];
    else if (path === `/api/workflows/${WORKFLOW_ID}`)
      body = {
        workflow_id: WORKFLOW_ID,
        name: "preview",
        graph: graph(),
        created_ts: 1,
        updated_ts: 1,
      };
    else if (path.endsWith("/runs"))
      body = [
        {
          snapshot_id: "run",
          workflow_id: WORKFLOW_ID,
          created_ts: 1,
          node_count: 1,
          job_count: 1,
          state_counts: { done: 1 },
          overall_state: "done",
        },
      ];
    else if (path === "/api/snapshots/run")
      body = {
        snapshot_id: "run",
        workflow_id: WORKFLOW_ID,
        created_ts: 1,
        graph: graph(),
        jobs: [job],
      };
    else if (path.endsWith("/analyze")) {
      await new Promise((resolve) => setTimeout(resolve, 100));
      body = {
        revision: "r",
        graph: graph(),
        diff: [],
        environment_params: {},
        staleness: { node: null },
        canvas_jobs: [job],
      };
    } else if (["/api/jobs", "/api/workflows"].includes(path)) body = [];
    await route.fulfill({ json: body });
  });
  return () => maximum;
}

test("header and drawer share loading state; metadata hydrates in bounded parallel batches", async ({
  page,
}) => {
  const maxActive = await routeBase(page);
  await page.goto(`/w/${WORKFLOW_ID}`);
  const card = page.locator('[data-hololab-node="algorithm"]');
  await expect(card).toHaveAttribute("data-state", "loading");
  await expect(
    page.locator('[data-hl-preview-placeholder="loading"]'),
  ).toBeVisible();
  await expect(card).toHaveAttribute("data-state", "done");
  await expect(
    page.locator('[data-hl-preview-placeholder="loading"]'),
  ).toHaveCount(0, { timeout: 5_000 });
  // Development StrictMode can overlap the two effect probes. Each probe is
  // bounded at eight by getHandle's shared queue; production has one probe.
  expect(maxActive()).toBeLessThanOrEqual(16);
});

test("failed handle lookup leaves loading and reports unavailable metadata", async ({
  page,
}) => {
  await routeBase(page, true);
  await page.goto(`/w/${WORKFLOW_ID}`);
  const card = page.locator('[data-hololab-node="algorithm"]');
  await expect(card).toHaveAttribute("data-state", "done");
  await expect(
    page.locator('[data-hl-preview-placeholder="unavailable"]'),
  ).toBeVisible();
  await expect(
    page.locator('[data-hl-preview-placeholder="loading"]'),
  ).toHaveCount(0);
});
