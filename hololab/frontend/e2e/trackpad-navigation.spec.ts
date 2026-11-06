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
  for (const [deltaX, deltaY, deltaMode] of [[18, 0, 0], [-18, 0, 0], [0, 18, 0], [0, -18, 0],
    [0, 1, 1], [0, -1, 2], [12, 24, 0], [0, 100, 0], [0, 1, 0], [0, 60, 0]]) {
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
  await page.reload();
  await expect(mode).toHaveAttribute("aria-pressed", "true");
  await mode.click();
  await expect(mode).toHaveAttribute("aria-pressed", "false");
  const restored = await transform();
  await wheel(0, 5);
  await expect.poll(async () => (await transform()).scale).not.toBe(restored.scale);
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
