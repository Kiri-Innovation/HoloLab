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
  const mode = page.locator(".react-flow__controls .hl-trackpad-navigation");
  await expect(mode).toHaveAttribute("aria-pressed", "false");
  await expect(mode).toHaveCount(1);
  await expect(mode).toBeVisible();
  const mouseIcon = await mode.locator("svg").innerHTML();
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
  const wheelAt = async (target: typeof pane, deltaX: number, deltaY: number, ctrlKey = false) => {
    await target.evaluate((el, init) => {
      el.dispatchEvent(new WheelEvent("wheel", { ...init, bubbles: true, cancelable: true, clientX: 600, clientY: 400 }));
    }, { deltaX, deltaY, ctrlKey });
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
  await mode.click();
  await expect(mode).toHaveAttribute("aria-pressed", "true");
  expect(await mode.locator("svg").innerHTML()).not.toBe(mouseIcon);
  // Node cards are siblings of the pane, but are still inside .react-flow.
  // Their XYFlow-provided `nopan` class must not hide trackpad wheel events.
  const node = page.locator(".react-flow__node").first();
  const nodeBefore = await transform();
  await wheelAt(node, 18, 24);
  await expect.poll(async () => (await transform()).x).not.toBe(nodeBefore.x);
  expect((await transform()).scale).toBeCloseTo(nodeBefore.scale, 6);
  for (const [deltaX, deltaY, deltaMode] of [[18, 0, 0], [0, 18, 0], [12, 24, 0]]) {
    const before = await transform();
    await wheel(deltaX, deltaY, false, deltaMode);
    await expect.poll(async () => {
      const after = await transform();
      return deltaX === 0 ? after.y : after.x;
    }).not.toBe(deltaX === 0 ? before.y : before.x);
    expect((await transform()).scale).toBeCloseTo(before.scale, 6);
    const after = await transform();
    if (deltaX !== 0) expect(Math.sign(after.x - before.x)).toBe(-Math.sign(deltaX));
    if (deltaY !== 0) expect(Math.sign(after.y - before.y)).toBe(-Math.sign(deltaY));
  }

  // macOS pinch is represented as Ctrl-wheel and remains React Flow zoom.
  const pinchBefore = await transform();
  await wheel(0, -18, true);
  await expect.poll(async () => (await transform()).scale).not.toBe(pinchBefore.scale);

  // Drawers follow trackpad navigation, while nested viewers remain insulated.
  const previewBefore = await transform();
  await node.evaluate((el) => {
    const drawer = document.createElement("div");
    drawer.className = "hl-preview-drawer";
    el.append(drawer);
    drawer.dispatchEvent(new WheelEvent("wheel", { deltaX: 12, deltaY: 18, bubbles: true, cancelable: true, clientX: 600, clientY: 400 }));
    drawer.remove();
  });
  await expect.poll(async () => (await transform()).y).not.toBe(previewBefore.y);
  expect((await transform()).scale).toBeCloseTo(previewBefore.scale, 6);

  const embeddedBefore = await transform();
  await node.evaluate((el) => {
    const drawer = document.createElement("div");
    drawer.className = "hl-preview-drawer";
    const embedded = document.createElement("div");
    embedded.className = "nowheel";
    drawer.append(embedded);
    el.append(drawer);
    embedded.dispatchEvent(new WheelEvent("wheel", { deltaY: 18, bubbles: true, cancelable: true, clientX: 600, clientY: 400 }));
    drawer.remove();
  });
  await page.waitForTimeout(200);
  expect(await transform()).toEqual(embeddedBefore);

  // XYFlow chrome is within .react-flow too, but is not canvas navigation.
  const controlsBefore = await transform();
  await wheelAt(page.locator(".react-flow__controls"), 0, 18);
  expect(await transform()).toEqual(controlsBefore);
  await page.getByRole("button", { name: "Show minimap" }).click();
  const minimapBefore = await transform();
  await wheelAt(page.locator(".react-flow__minimap"), 0, 18);
  expect(await transform()).toEqual(minimapBefore);

  mkdirSync("/tmp/trackpad-navigation", { recursive: true });
  await page.screenshot({ path: "/tmp/trackpad-navigation/gesture-simulation.png" });
  writeFileSync("/tmp/trackpad-navigation/wheel-events.json", JSON.stringify(logs, null, 2));
  await page.reload();
  await expect(mode).toHaveAttribute("aria-pressed", "true");
  await mode.click();
  await expect(mode).toHaveAttribute("aria-pressed", "false");
  const restored = await transform();
  await wheel(0, 5);
  await expect.poll(async () => (await transform()).scale).not.toBe(restored.scale);
  const nodeMouseBefore = await transform();
  await wheelAt(node, 0, 5);
  await expect.poll(async () => (await transform()).scale).not.toBe(nodeMouseBefore.scale);
  for (const theme of ["light", "dark"]) {
    await page.evaluate(value => { document.documentElement.dataset.theme = value; }, theme);
    for (const enabled of [false, true]) {
      if ((await mode.getAttribute("aria-pressed")) !== String(enabled)) await mode.click();
      await pane.hover();
      await mode.hover();
      const label = enabled ? "切换回鼠标导航（滚轮缩放）" : "切换为触控板导航（双指滑动平移、捏合缩放）";
      await expect(mode).toHaveAttribute("aria-label", label);
      await expect(page.getByRole("tooltip")).toHaveText(enabled
        ? "触控板导航：滚轮/双指滑动平移画布（Ctrl+滚轮或捏合缩放）；点击切换回鼠标导航" : label);
      if (!enabled) expect(await mode.locator("svg").innerHTML()).toBe(mouseIcon);
      expect(await mode.getAttribute("title")).toBeNull();
      const controls = page.locator(".react-flow__controls");
      const sizes = await controls.locator("button").evaluateAll(buttons => buttons.map(button => {
        const box = button.getBoundingClientRect();
        return { width: box.width, height: box.height };
      }));
      expect(sizes.every(size => size.width === sizes[0].width && size.height === sizes[0].height)).toBe(true);
      await page.screenshot({ path: `/tmp/trackpad-navigation/${theme}-${enabled ? "on" : "off"}.png` });
      await pane.hover();
      await controls.screenshot({ path: `/tmp/trackpad-navigation/${theme}-${enabled ? "on" : "off"}-controls.png` });
    }
  }
});

test("real 80-image drawer follows canvas mode and reports scroll dimensions", async ({ page }, info) => {
  const pack = {
    name: "drawer-test", version: "1", manifest_hash: "drawer", node_ids: ["device"],
    inputs: {}, outputs: { images: { tags: ["image"], arrayed: true, dim_labels: ["frame"], preview: null } },
    params: { count: { type: "int", default: 17, pinned: true, optional: false } },
  };
  const graph = { nodes: [{ id: "drawer", algorithm_name: pack.name, algorithm_version: "1",
    assigned_node_id: "device", params: { count: 17 }, preview_open: "images", position: { x: 0, y: 0 } }], edges: [] };
  await page.routeWebSocket("**", () => {});
  await page.addInitScript(() => { delete (window as { IntersectionObserver?: unknown }).IntersectionObserver; });
  await page.route("**/proxy/device/**", route => route.fulfill({ status: 404 }));
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = [];
    if (path === "/api/pack-catalog") body = [pack];
    else if (path === "/api/nodes/metrics/history") body = { nodes: {}, sample_interval_s: 3 };
    else if (path === "/api/workflows/drawer") body = { workflow_id: "drawer", name: "80 image drawer", graph, created_ts: 1, updated_ts: 1 };
    else if (path === "/api/workflows/drawer/canvas-state") body = { staleness: {}, canvas_jobs: [{
      job_id: "drawer-job", graph_node_id: "drawer", node_id: "device", workflow_id: "drawer", algorithm_name: pack.name,
      algorithm_version: "1", state: "done", params: { count: 17 }, input_handles: {}, output_handles: { images: "images" }, created_ts: 1,
    }] };
    else if (path === "/api/workflows/drawer/runs") body = [{ snapshot_id: "drawer-run", workflow_id: "drawer", created_ts: 1,
      state_counts: { done: 1 }, overall_state: "done", node_count: 1, job_count: 1 }];
    else if (path === "/api/snapshots/drawer-run") body = { snapshot_id: "drawer-run", workflow_id: "drawer", graph, created_ts: 1,
      jobs: [{ job_id: "drawer-job", graph_node_id: "drawer", node_id: "device", workflow_id: "drawer", algorithm_name: pack.name,
        algorithm_version: "1", state: "done", params: { count: 17 }, input_handles: {}, output_handles: { images: "images" }, created_ts: 1 }] };
    else if (path === "/api/handles/images") body = { handle_id: "images", node_id: "device", storage: "dir", tags: ["image"],
      proxy_url: "/proxy/device/images", absolute_path: "/images", deleted_ts: null, preview: null, dim_labels: ["frame"], dim_sizes: [80] };
    else if (path === "/api/handles/images/summary") body = { fields: { entries: Array.from({ length: 80 }, (_, i) => ({ name: `image-${i}.jpg`, is_dir: false })) } };
    await route.fulfill({ json: body });
  });
  await page.goto("/w/drawer");
  const drawer = page.locator(".react-flow__node .hl-preview-drawer");
  await expect(drawer.locator("[data-hl-frame-count]")).toHaveAttribute("data-hl-frame-count", "80");
  const dimensions = await drawer.evaluate(el => [el, ...el.querySelectorAll("*")].map(element => ({
    tag: element.tagName, classes: element.getAttribute("class"),
    scrollHeight: element.scrollHeight, clientHeight: element.clientHeight,
    overflowY: getComputedStyle(element).overflowY,
  })));
  console.log("80-image drawer scroll dimensions:", JSON.stringify(dimensions));
  await info.attach("drawer-scroll-dimensions", { body: JSON.stringify(dimensions, null, 2), contentType: "application/json" });
  const viewport = page.locator(".react-flow__viewport");
  const transform = () => viewport.evaluate(el => {
    const matrix = new DOMMatrixReadOnly(getComputedStyle(el).transform);
    return { x: matrix.m41, y: matrix.m42, scale: matrix.a };
  });
  const wheelAt = (target: typeof drawer, ctrlKey = false) => target.evaluate((el, ctrlKey) => {
    el.dispatchEvent(new WheelEvent("wheel", { deltaX: 2.67, deltaY: 1.33, ctrlKey, bubbles: true, cancelable: true, clientX: 600, clientY: 400 }));
  }, ctrlKey);
  const mode = page.locator(".hl-trackpad-navigation");
  const mouseBefore = await transform();
  await wheelAt(drawer);
  await expect.poll(async () => (await transform()).scale).not.toBe(mouseBefore.scale);
  await mode.click();
  const panBefore = await transform();
  await wheelAt(drawer);
  await expect.poll(async () => (await transform()).x).not.toBe(panBefore.x);
  expect((await transform()).scale).toBeCloseTo(panBefore.scale, 6);
  const pinned = page.getByRole("group", { name: "Pinned parameters", exact: true });
  await expect(pinned).toBeVisible();
  const pinnedBefore = await transform();
  await wheelAt(pinned);
  await expect.poll(async () => (await transform()).scale).not.toBe(pinnedBefore.scale);
  await expect(pinned.getByRole("spinbutton")).toHaveValue("17");
  const pinchBefore = await transform();
  await wheelAt(drawer, true);
  await expect.poll(async () => (await transform()).scale).not.toBe(pinchBefore.scale);
});
