import { expect, test } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";

test.use({ viewport: { width: 1400, height: 950 } });

test("explicit mode keeps variable mouse sequences zooming and trackpad sequences panning", async ({ page }) => {
  const pack = {
    name: "trackpad-test", version: "1", manifest_hash: "trackpad", node_ids: ["device"],
    inputs: {}, outputs: {}, params: {},
  };
  const graph = {
    nodes: [{ id: "trackpad", algorithm_name: pack.name, algorithm_version: pack.version, assigned_node_id: "device", params: {}, position: { x: 0, y: 0 } }],
    edges: [],
  };
  await page.routeWebSocket("**", () => {});
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = [];
    if (path === "/api/pack-catalog") body = [pack];
    else if (path === "/api/nodes/metrics/history") body = { nodes: {}, sample_interval_s: 3 };
    else if (path === "/api/workflows/trackpad") body = { workflow_id: "trackpad", name: "Trackpad verification", graph, created_ts: 1, updated_ts: 1 };
    await route.fulfill({ json: body });
  });

  await page.goto("/w/trackpad");
  const pane = page.locator(".react-flow__pane");
  const viewport = page.locator(".react-flow__viewport");
  await expect(pane).toBeVisible();
  const mode = page.getByRole("checkbox", { name: "触控板导航" });
  await expect(mode).not.toBeChecked();
  const logs: unknown[] = [];
  const transform = () => viewport.evaluate((el) => {
    const values = getComputedStyle(el).transform.match(/matrix\(([^)]+)\)/)?.[1].split(",").map(Number);
    if (!values) throw new Error("React Flow viewport did not have a matrix transform");
    return { scale: values[0], x: values[4], y: values[5] };
  });
  const wheel = async (deltaX: number, deltaY: number, ctrlKey = false, deltaMode = 0) => {
    const before = await transform();
    await pane.evaluate((el, init) => {
    el.dispatchEvent(new WheelEvent("wheel", { ...init, bubbles: true, cancelable: true, clientX: 600, clientY: 400 }));
    }, { deltaX, deltaY, ctrlKey, deltaMode });
    logs.push({ deltaX, deltaY, deltaMode, ctrlKey, before, after: await transform() });
  };

  for (const sequence of [[100, 100, 100], [1, 2, 5, 10, 4, 1], [40, 60, 20, 100]]) {
    for (const delta of sequence) {
      const before = await transform();
      await wheel(0, delta);
      await expect.poll(async () => (await transform()).scale).not.toBe(before.scale);
    }
  }

  // A conventional wheel-sized event retains the baseline React Flow zoom.
  const mouseBefore = await transform();
  await pane.hover();
  await page.mouse.wheel(0, 100);
  await expect.poll(async () => (await transform()).scale).not.toBe(mouseBefore.scale);

  // The existing left-button drag pan path is unrelated to wheel handling.
  const paneBox = (await pane.boundingBox())!;
  const dragBefore = await transform();
  await page.mouse.move(paneBox.x + paneBox.width - 80, paneBox.y + 80);
  await page.mouse.down();
  await page.mouse.move(paneBox.x + paneBox.width - 40, paneBox.y + 120);
  await page.mouse.up();
  await expect.poll(async () => (await transform()).x).not.toBe(dragBefore.x);

  // These are the small pixel events Chrome sends for a two-finger trackpad
  // scroll. Every cardinal direction changes translation without scale.
  await mode.check();
  const lineBefore = await transform();
  await wheel(0, 1, false, 1);
  await expect.poll(async () => (await transform()).scale).not.toBe(lineBefore.scale);
  for (const [deltaX, deltaY] of [[18, 0], [-18, 0], [0, 18], [0, -18]]) {
    const before = await transform();
    await wheel(deltaX, deltaY);
    await expect.poll(async () => {
      const after = await transform();
      return deltaX === 0 ? after.y : after.x;
    }).not.toBe(deltaX === 0 ? before.y : before.x);
    expect((await transform()).scale).toBeCloseTo(before.scale, 6);
  }

  // macOS pinch is represented as Ctrl-wheel and remains React Flow zoom.
  const pinchBefore = await transform();
  await wheel(0, -18, true);
  await expect.poll(async () => (await transform()).scale).not.toBe(pinchBefore.scale);

  // Preview drawers deliberately retain their historical wheel-to-zoom path;
  // embedded nowheel views remain fully insulated from canvas navigation.
  const previewBefore = await transform();
  await pane.evaluate((el) => {
    const drawer = document.createElement("div");
    drawer.className = "hl-preview-drawer";
    el.append(drawer);
    drawer.dispatchEvent(new WheelEvent("wheel", { deltaY: 18, bubbles: true, cancelable: true, clientX: 600, clientY: 400 }));
    drawer.remove();
  });
  await expect.poll(async () => (await transform()).scale).not.toBe(previewBefore.scale);

  const embeddedBefore = await transform();
  await pane.evaluate((el) => {
    const embedded = document.createElement("div");
    embedded.className = "nowheel";
    el.append(embedded);
    embedded.dispatchEvent(new WheelEvent("wheel", { deltaY: 18, bubbles: true, cancelable: true, clientX: 600, clientY: 400 }));
    embedded.remove();
  });
  await page.waitForTimeout(200);
  expect(await transform()).toEqual(embeddedBefore);

  mkdirSync("/tmp/trackpad-navigation", { recursive: true });
  await page.screenshot({ path: "/tmp/trackpad-navigation/gesture-simulation.png" });
  writeFileSync("/tmp/trackpad-navigation/wheel-events.json", JSON.stringify(logs, null, 2));
  await mode.uncheck();
  const restored = await transform();
  await wheel(0, 5);
  await expect.poll(async () => (await transform()).scale).not.toBe(restored.scale);
});
