// Regression for the "Agent 与 UI 能力须对称" invariant.
//
// Old workflow fields must not restore the removed batching control.

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
    params: { checkpoint: {type: "path", optional: true, default: null}, video_dir: {type: "path", optional: false, default: ""} },
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
        params: { checkpoint: null, video_dir: "/data" },
        local_params: ["checkpoint", "video_dir"],
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
  await page.routeWebSocket("**/*", () => {});
  await page.route(`**/api/workflows/${WORKFLOW_ID}/canvas-state`, jsonRoute({ canvas_jobs: [], staleness: {} }));
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

test("optional path stays blank and clearing removes its key; required data path remains editable", async ({page}) => {
  await stubCommon(page);
  const saved: any[] = [];
  await page.route(/\/api\/workflows$/, async route => {
    if(route.request().method()==="POST") {
      saved.push(route.request().postDataJSON());
      await route.fulfill({json:{workflow_id:WORKFLOW_ID,name:"optional path",updated_ts:CREATED_TS+saved.length}});
    } else await route.fulfill({json:[]});
  });
  await page.route(`**/api/workflows/${WORKFLOW_ID}`, jsonRoute({workflow_id:WORKFLOW_ID,name:"optional path",graph:draftGraph({arrayed_toggle:false}),created_ts:CREATED_TS,updated_ts:CREATED_TS}));
  await page.goto(`/w/${WORKFLOW_ID}`);
  await page.locator('[data-hololab-node="algorithm"]').click();
  await page.getByRole('tab',{name:'Inspector',exact:true}).click();
  const optional=page.getByRole('combobox',{name:'checkpoint',exact:true});
  await expect(optional).toHaveValue('');
  await expect(page.getByRole('combobox',{name:'video_dir',exact:true})).toHaveValue('/data');
  await optional.fill('/weights');await optional.blur();
  await expect.poll(()=>saved.at(-1)?.graph.nodes[0].params.checkpoint).toBe('/weights');
  await optional.fill('');await optional.blur();
  await expect.poll(()=>saved.at(-1)?.graph.nodes[0].params).toEqual({video_dir:'/data'});
  await expect(optional).toHaveValue('');
  const required=page.getByRole('combobox',{name:'video_dir',exact:true});
  await required.fill('/new-data');await required.blur();
  await expect.poll(()=>saved.at(-1)?.graph.nodes[0].params).toEqual({video_dir:'/new-data'});
});
