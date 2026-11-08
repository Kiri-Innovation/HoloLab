import {
  test,
  expect,
  type BrowserContext,
  type WebSocketRoute,
} from "@playwright/test";
import { applyOps } from "../src/canvas/workflowPatch";

async function fixture(context: BrowserContext) {
  let doc: any = {
    id: "file-test",
    workflow_id: "file-test",
    name: "Original",
    path: "/tmp/Original.workflow",
    missing: false,
    rev: 0,
    updated_ts: 1,
    created_ts: 1,
    graph: {
      nodes: [
        {
          id: "a",
          algorithm_name: "demo",
          algorithm_version: "1",
          assigned_node_id: "compute",
          position: { x: 50, y: 70 },
          params: { count: 1 },
        },
      ],
      edges: [],
      pinned_params: { "demo@1": ["count"] },
    },
  };
  const sockets: WebSocketRoute[] = [];
  const patches: any[] = [];
  let posts = 0;
  let holdSaves = false;
  let runCalls = 0;
  const broadcast = (extra: any = {}) =>
    sockets.forEach((ws) =>
      ws.send(
        JSON.stringify({
          kind: "workflow_changed",
          payload: { ...doc, ...extra },
        }),
      ),
    );
  await context.routeWebSocket("**", (ws) => {
    sockets.push(ws);
    ws.onMessage((raw) => {
      const frame = JSON.parse(String(raw));
      if (frame.kind !== "workflow_edit") return;
      const p = frame.payload;
      patches.push(p);
      if (p.base_rev !== doc.rev) {
        ws.send(
          JSON.stringify({
            kind: "workflow_changed",
            payload: {
              ...doc,
              request_id: p.request_id,
              rejected: true,
              error: "base_rev is behind current revision",
            },
          }),
        );
        return;
      }
      doc = { ...doc, ...applyOps(doc, p.ops), rev: doc.rev + 1 };
      broadcast({ request_id: p.request_id });
      if (!holdSaves) setTimeout(() => broadcast({ saved: true }), 20);
    });
  });
  await context.route("**/api/**", async (route) => {
    const req = route.request(),
      path = new URL(req.url()).pathname;
    let body: any = [];
    if (path === "/api/workflows" && req.method() === "POST") posts++;
    if (path === "/api/workflows/file-test") body = doc;
    else if (path === "/api/workflows")
      body = [{ ...doc, node_count: 1, last_run: null }];
    else if (path === "/api/pack-catalog")
      body = [
        {
          name: "demo",
          version: "1",
          manifest_hash: "hash",
          node_ids: ["compute"],
          inputs: {},
          outputs: {},
          params: { count: { type: "int", default: 1, pinned: true } },
          arrayable: false,
        },
      ];
    else if (path === "/api/nodes/metrics/history")
      body = { sample_interval_s: 3, nodes: {} };
    else if (path === "/api/nodes/gateway/paths")
      body = { path: "/tmp", parent: "/", entries: [], next_offset: null };
    else if (path === "/api/workflow-files")
      body = { id: "created", path: "/tmp/new.workflow" };
    else if (path === "/api/workflow-files/open")
      body = { id: "opened", path: "/tmp/open.workflow" };
    else if (path === "/api/workflows/file-test/run") {
      runCalls++;
      body = { snapshot_id: null };
    } else if (path === "/api/workflows/file-test/recreate") {
      doc = {
        ...doc,
        missing: false,
        rev: doc.rev + 1,
        graph: { nodes: [], edges: [] },
      };
      body = { id: doc.id, path: doc.path };
    }
    await route.fulfill({ json: body });
  });
  return {
    patches,
    broadcast,
    get: () => doc,
    set: (next: any) => {
      doc = { ...doc, ...next };
    },
    posts: () => posts,
    holdSaves: () => {
      holdSaves = true;
    },
    releaseSave: () => {
      holdSaves = false;
      broadcast({ saved: true });
    },
    runCalls: () => runCalls,
  };
}

test("Run waits for durable file save rather than only the patch ACK", async ({
  context,
  page,
}) => {
  const server = await fixture(context);
  await page.goto("/w/file-test");
  const count = page
    .locator('.react-flow__node[data-id="a"]')
    .getByLabel("count", { exact: true });
  await expect(count).toHaveValue("1");
  server.holdSaves();
  await count.fill("8");
  await expect.poll(() => server.get().graph.nodes[0].params.count).toBe(8);
  await page.getByRole("button", { name: "运行流程", exact: true }).click();
  await page.waitForTimeout(250);
  expect(server.runCalls()).toBe(0);
  server.releaseSave();
  await expect.poll(() => server.runCalls()).toBe(1);
});

test("canvas patches sync two tabs, external reload, missing dialog and recreate", async ({
  context,
  page,
}) => {
  const server = await fixture(context);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/w/file-test");
  await expect(page.getByPlaceholder("workflow name")).toHaveValue("Original");
  const other = await context.newPage();
  await other.goto("/w/file-test");
  const count = page
    .locator('.react-flow__node[data-id="a"]')
    .getByLabel("count", { exact: true });
  await expect(count).toHaveValue("1");
  await count.fill("7");
  await expect.poll(() => server.get().graph.nodes[0].params.count).toBe(7);
  await expect(
    other
      .locator('.react-flow__node[data-id="a"]')
      .getByLabel("count", { exact: true }),
  ).toHaveValue("7");
  await expect(page.locator('[data-hl-save-status="saved"]')).toBeVisible();
  expect(
    server.patches.some((p) =>
      p.ops.some((o: any) => o.op === "node.map.set" && o.key === "count"),
    ),
  ).toBe(true);
  expect(server.posts()).toBe(0);
  server.set({ name: "External disk change", rev: server.get().rev + 1 });
  server.broadcast();
  await expect(page.getByPlaceholder("workflow name")).toHaveValue(
    "External disk change",
  );
  await expect(other.getByPlaceholder("workflow name")).toHaveValue(
    "External disk change",
  );
  server.set({ missing: true, rev: server.get().rev + 1 });
  server.broadcast();
  const recovery = page.getByRole("dialog", {
    name: "Workflow 文件丢失或身份不匹配",
  });
  await expect(recovery).toBeVisible();
  await expect(
    recovery.getByRole("button", { name: "重新定位（relink）" }),
  ).toBeVisible();
  await recovery.getByRole("button", { name: "原路径新建空 wf" }).click();
  await expect(recovery).toHaveCount(0);
  await expect(page.locator(".react-flow__node")).toHaveCount(0);
  expect(errors).toEqual([]);
  await other.close();
});

test("stale base replays local fields over current authoritative document", async ({
  context,
  page,
}) => {
  const server = await fixture(context);
  await page.goto("/w/file-test");
  const count = page
    .locator('.react-flow__node[data-id="a"]')
    .getByLabel("count", { exact: true });
  await expect(count).toHaveValue("1");
  // Change server truth without delivering the broadcast; the next edit
  // must be rejected, rebased and retried instead of replacing the graph.
  server.set({ name: "Remote rename", rev: 1 });
  await count.fill("9");
  await expect.poll(() => server.get().graph.nodes[0].params.count).toBe(9);
  expect(server.get().name).toBe("Remote rename");
  await expect(
    page.getByRole("button", { name: "重载放弃本地", exact: true }).first(),
  ).toBeVisible();
  expect(server.patches[0].base_rev).toBe(0);
  expect(server.patches.at(-1).base_rev).toBe(1);
});

test("new and open workflow use gateway PathInput and file API", async ({
  context,
  page,
}) => {
  await fixture(context);
  await page.goto("/");
  await page
    .getByRole("button", { name: "+ 新建 workflow", exact: true })
    .click();
  const create = page.getByRole("dialog", { name: "新建 workflow 文件" });
  await expect(create).toBeVisible();
  await expect(create).toHaveAttribute("aria-modal", "true");
  await expect(create).toHaveCSS("position", "fixed");
  const box = await create.locator(":scope > div").boundingBox();
  expect(box).not.toBeNull();
  expect(Math.abs((box!.x + box!.width / 2) - 640)).toBeLessThan(2);
  expect(Math.abs((box!.y + box!.height / 2) - 360)).toBeLessThan(2);
  await create.click({ position: { x: 4, y: 4 } });
  await expect(create).toBeVisible();
  await create.getByLabel("Workflow 文件名").fill("new");
  const request = page.waitForRequest(
    (r) => new URL(r.url()).pathname === "/api/workflow-files",
  );
  await create.getByLabel("Workflow 文件名").press("Enter");
  expect((await request).postDataJSON()).toEqual({
    parent_dir: "/cloud/cloud-ssd1/Kiri4DGS",
    name: "new",
  });
  await page.goto("/");
  await page
    .getByRole("button", { name: "打开 workflow", exact: true })
    .click();
  const open = page.getByRole("dialog", { name: "打开 workflow 文件" });
  await open.locator("input").first().fill("/tmp/open.workflow");
  const opening = page.waitForRequest(
    (r) => new URL(r.url()).pathname === "/api/workflow-files/open",
  );
  await open.getByRole("button", { name: "打开", exact: true }).click();
  expect((await opening).postDataJSON()).toEqual({
    path: "/tmp/open.workflow",
  });
});

test("workflow file modals cancel with Escape or Cancel without requests", async ({
  context,
  page,
}) => {
  await fixture(context);
  let requests = 0;
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/api/workflow-files")) requests++;
  });
  await page.goto("/");
  await page.getByRole("button", { name: "+ 新建 workflow", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "新建 workflow 文件" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "新建 workflow 文件" })).toHaveCount(0);
  await page.getByRole("button", { name: "打开 workflow", exact: true }).click();
  const open = page.getByRole("dialog", { name: "打开 workflow 文件" });
  await open.getByRole("button", { name: "取消", exact: true }).click();
  await expect(open).toHaveCount(0);
  expect(requests).toBe(0);
});
