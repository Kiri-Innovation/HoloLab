import { test, expect } from "@playwright/test";
import { spawn, type ChildProcess } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

let gateway: ChildProcess;
let origin: string;
let temporary: string;

test.describe.configure({ mode: "serial" });
test.use({ viewport: { width: 1440, height: 1000 } });
test.beforeAll(async ({ request }) => {
  temporary = await mkdtemp(path.join(tmpdir(), "hololab-node-defaults-"));
  gateway = spawn(process.env.PYTHON ?? "python", [
    "hololab/frontend/e2e/fixtures/node-defaults-gateway.py", temporary,
  ], { cwd: path.resolve(process.cwd(), "../.."), env: { ...process.env, PYTHONPATH: "." } });
  let logs = "";
  origin = await new Promise<string>((resolve, reject) => {
    gateway.stdout!.on("data", chunk => {
      logs += chunk.toString();
      const match = logs.match(/GATEWAY_PORT=(\d+)/);
      if (match) resolve(`http://127.0.0.1:${match[1]}`);
    });
    gateway.stderr!.on("data", chunk => { logs += chunk.toString(); });
    gateway.on("error", reject);
    gateway.on("exit", code => reject(new Error(`Gateway exited ${code}: ${logs}`)));
  });
  await expect.poll(async () => {
    try { return (await request.get(`${origin}/api/health`)).ok(); } catch { return false; }
  }).toBe(true);
});
test.afterAll(async () => {
  if (gateway && gateway.exitCode === null) {
    const stopped = new Promise(resolve => gateway.once("exit", resolve));
    gateway.kill("SIGTERM");
    await stopped;
  }
  if (temporary) await rm(temporary, { recursive: true, force: true });
});

for (const [suffix, parallelism, batch_size] of [
  ["recommended", 4, 2], ["omitted", 1, 1], ["null", 1, 1],
] as const) {
  test(`palette drop persists ${suffix} recommendations and preserves user edits`, async ({ page, request }) => {
    // All HTTP API requests reach the isolated real gateway; no catalog/save mocks.
    await page.route("**/api/**", async route => {
      const url = new URL(route.request().url());
      await route.fulfill({ response: await route.fetch({ url: origin + url.pathname + url.search }) });
    });
    await page.routeWebSocket("**/*", () => {});
    const created = await request.post(`${origin}/api/workflows`, {
      data: { name: `Temporary node defaults ${suffix}`, graph: { nodes: [], edges: [] } },
    });
    expect(created.ok()).toBe(true);
    const { workflow_id } = await created.json();
    const url = `${origin}/api/workflows/${workflow_id}`;
    const read = async () => (await request.get(url)).json();
    try {
      await page.goto(`/w/${workflow_id}`);
      await page.locator(".hl-pack-palette").getByRole("searchbox").fill(`defaults-${suffix}`);
      const leaf = page.locator(".hl-pack-palette .hl-pack-leaf");
      await expect(leaf).toHaveCount(1);
      const bounds = (await page.locator(".react-flow__pane").boundingBox())!;
      await leaf.evaluate((el, point) => {
        const dataTransfer = new DataTransfer();
        el.dispatchEvent(new DragEvent("dragstart", { bubbles: true, dataTransfer }));
        document.querySelector(".react-flow__pane")!.dispatchEvent(new DragEvent("drop", {
          bubbles: true, cancelable: true, dataTransfer, clientX: point.x, clientY: point.y,
        }));
      }, { x: bounds.x + 300, y: bounds.y + 250 });
      await expect.poll(async () => (await read()).graph.nodes).toEqual([
        expect.objectContaining({ parallelism, batch_size, arrayed_toggle: false }),
      ]);
      const saved = await read();
      console.log("API_READBACK " + JSON.stringify({ workflow_id, nodes: saved.graph.nodes.map(
        ({ id, algorithm_name, algorithm_version, parallelism, batch_size, arrayed_toggle }: any) =>
          ({ id, algorithm_name, algorithm_version, parallelism, batch_size, arrayed_toggle }),
      ) }));
      await page.locator('[data-hololab-node="algorithm"]').click();
      await page.locator("input[data-hl-arrayed-toggle]").check();
      await page.locator("input[data-hl-parallelism]").fill("7");
      await page.locator("input[data-hl-batch-size]").fill("3");
      await page.locator("input[data-hl-batch-size]").blur();
      await expect.poll(async () => (await read()).graph.nodes[0]).toMatchObject({ parallelism: 7, batch_size: 3 });
      await page.reload();
      await page.locator('[data-hololab-node="algorithm"]').click();
      await expect(page.locator("input[data-hl-parallelism]")).toHaveValue("7");
      await expect(page.locator("input[data-hl-batch-size]")).toHaveValue("3");
    } finally {
      await page.goto("about:blank");
      expect((await request.delete(url)).ok()).toBe(true);
      expect((await request.get(url)).status()).toBe(404);
    }
  });
}
