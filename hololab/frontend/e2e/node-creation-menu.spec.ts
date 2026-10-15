import { test, expect, catalog } from "./fixtures/node-creation";

test.use({ viewport: { width: 1440, height: 1000 } });
const screenshots = "e2e/screenshots/node-creation";

test("search, keyboard creation and palette drops share pan/zoom coordinates", async ({ page }) => {
  const pane = page.locator(".react-flow__pane");
  const bounds = (await pane.boundingBox())!;
  const point = { x: Math.round(bounds.x + bounds.width * 0.42), y: Math.round(bounds.y + bounds.height * 0.4) };
  await page.mouse.move(point.x, point.y);
  await page.mouse.wheel(0, 320);
  await page.waitForTimeout(400);
  await page.mouse.down();
  await page.mouse.move(point.x + 65, point.y + 45, { steps: 8 });
  await page.mouse.up();
  const viewport = page.locator(".react-flow__viewport");
  const matrix = await viewport.evaluate(el => {
    const m = new DOMMatrix(getComputedStyle(el).transform);
    return { x: m.e, y: m.f, zoom: m.a };
  });
  expect(matrix.zoom).not.toBe(1);
  const transform = await viewport.getAttribute("style");
  await page.mouse.click(point.x, point.y, { button: "right" });
  const menu = page.getByRole("dialog", { name: "创建节点" });
  const search = menu.getByRole("combobox");
  await expect(search).toBeFocused();
  await expect(menu.getByRole("option")).toHaveCount(catalog.length + 1);
  await expect(viewport).toHaveAttribute("style", transform!);
  const menuBounds = (await menu.boundingBox())!;
  expect(menuBounds.x).toBe(point.x);
  expect(menuBounds.height).toBeLessThanOrEqual(480);
  for (const theme of ["light", "dark"]) {
    await page.evaluate(theme => document.documentElement.dataset.theme = theme, theme);
    await page.waitForTimeout(250); // allow theme color transitions to finish
    await page.screenshot({ path: `${screenshots}/expanded-${theme}.png` });
  }
  await search.press("ArrowUp");
  await expect(menu.getByRole("option", { selected: true })).toContainText("utility-19");
  await expect.poll(() => menu.locator(".hl-node-creation-list").evaluate(el => el.scrollTop)).toBeGreaterThan(0);
  await search.press("ArrowDown");
  await menu.getByRole("button", { name: "utilities" }).click();
  await expect(menu.getByRole("option")).toHaveCount(3);
  await search.fill("TRANSFORM");
  await expect(menu.getByRole("option")).toHaveText(["image-resizev1.0.0"]);
  await search.fill("image-");
  await expect(menu.getByRole("option")).toHaveCount(2);
  await search.press("ArrowDown");
  await expect(menu.getByRole("option", { selected: true })).toContainText("image-resize");
  await search.press("ArrowUp");
  await expect(menu.getByRole("option", { selected: true })).toContainText("image-blend");
  await search.fill("image-resize");
  await page.screenshot({ path: `${screenshots}/search.png` });
  await search.press("Enter");
  await expect(menu).toHaveCount(0);
  const node = page.locator(".react-flow__node-algorithm").first();
  await expect(node).toBeVisible();
  const nodeBounds = (await node.boundingBox())!;
  expect(nodeBounds.x).toBeCloseTo(point.x, 0);
  expect(nodeBounds.y).toBeCloseTo(point.y, 0);
  const flowPosition = await node.evaluate(el => {
    const m = new DOMMatrix(getComputedStyle(el).transform);
    return { x: m.e, y: m.f };
  });
  expect(flowPosition.x).toBeCloseTo((point.x - bounds.x - matrix.x) / matrix.zoom, 0);
  expect(flowPosition.y).toBeCloseTo((point.y - bounds.y - matrix.y) / matrix.zoom, 0);
  await page.screenshot({ path: `${screenshots}/created-position.png` });

  const dropPoint = { x: point.x + 210, y: point.y + 110 };
  await page.locator(".hl-pack-palette").getByRole("searchbox").fill("image-resize");
  await page.locator(".hl-pack-palette .hl-pack-leaf").evaluate((el, point) => {
    const dataTransfer = new DataTransfer();
    el.dispatchEvent(new DragEvent("dragstart", { bubbles: true, dataTransfer }));
    document.querySelector(".react-flow__pane")!.dispatchEvent(new DragEvent("drop", {
      bubbles: true, cancelable: true, dataTransfer, clientX: point.x, clientY: point.y,
    }));
  }, dropPoint);
  await expect(page.locator(".react-flow__node-algorithm")).toHaveCount(2);
  const dropped = (await page.locator(".react-flow__node-algorithm").last().boundingBox())!;
  expect(dropped.x).toBeCloseTo(dropPoint.x, 0);
  expect(dropped.y).toBeCloseTo(dropPoint.y, 0);
});

test("dismissal, builtin, edge clamping and right-button isolation", async ({ page }) => {
  const pane = page.locator(".react-flow__pane");
  const bounds = (await pane.boundingBox())!;
  const point = { x: bounds.x + 80, y: bounds.y + 100 };
  const open = () => page.mouse.click(point.x, point.y, { button: "right" });
  const menu = page.getByRole("dialog", { name: "创建节点" });
  await open();
  await menu.getByRole("combobox").fill("no-such-pack");
  await expect(menu.getByRole("status")).toHaveText("无匹配");
  await menu.getByRole("combobox").press("Enter");
  await expect(page.locator(".react-flow__node")).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(menu).toHaveCount(0);
  await open();
  await page.locator(".hl-pack-palette").getByRole("searchbox").click();
  await expect(menu).toHaveCount(0);
  await open();
  await menu.getByRole("combobox").fill("visualize");
  await menu.getByRole("option").click();
  await expect(page.locator(".react-flow__node-view")).toHaveCount(1);
  await expect(menu).toHaveCount(0);
  await page.locator(".react-flow__node-view").click({ button: "right" });
  await expect(menu).toHaveCount(0);
  await page.locator(".react-flow__handle").first().dispatchEvent("contextmenu", { button: 2 });
  await expect(menu).toHaveCount(0);
  await page.locator(".hl-pack-palette").click({ button: "right" });
  await expect(menu).toHaveCount(0);
  const viewport = page.locator(".react-flow__viewport");
  const transform = await viewport.getAttribute("style");
  await page.mouse.move(bounds.x + bounds.width - 100, bounds.y + bounds.height - 100);
  await page.mouse.down({ button: "right" });
  await page.mouse.move(bounds.x + bounds.width - 20, bounds.y + bounds.height - 20, { steps: 8 });
  await page.mouse.up({ button: "right" });
  await expect(viewport).toHaveAttribute("style", transform!);
  await expect(menu).toBeVisible();
  await page.keyboard.press("Escape");
  // Chromium on Linux opens contextmenu on mouse-down; test edge placement
  // with a separate click rather than assuming it opens on right-button release.
  await page.mouse.click(bounds.x + bounds.width - 20, bounds.y + bounds.height - 80, { button: "right" });
  const rect = (await menu.boundingBox())!;
  expect(rect.x + rect.width).toBeLessThanOrEqual(1440 - 8);
  expect(rect.y + rect.height).toBeLessThanOrEqual(1000 - 8);
  await menu.getByRole("combobox").fill("visualize");
  await page.keyboard.press("Enter");
  const created = (await page.locator(".react-flow__node-view").last().boundingBox())!;
  expect(created.x).toBeCloseTo(bounds.x + bounds.width - 20, 0);
  expect(created.y).toBeCloseTo(bounds.y + bounds.height - 80, 0);
});


test("existing edges, ports and canvas controls retain their context events", async ({ page }) => {
  await page.route("**/api/workflows/menu-fixture", route => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      workflow_id: "menu-fixture", name: "Existing graph", created_ts: 1, updated_ts: 1,
      graph: {
        nodes: ["source", "target"].map((id, i) => ({
          id, kind: "algorithm", algorithm_name: "image-resize", algorithm_version: "1.0.0",
          params: {}, position: { x: i * 450, y: 100 }, assigned_node_id: null,
        })),
        edges: [{ id: "link", source: "source", sourceHandle: "out", target: "target", targetHandle: "in" }],
      },
    }),
  }));
  await page.reload();
  const edge = page.locator(".react-flow__edge-interaction");
  await expect(edge).toHaveCount(1);
  const menu = page.getByRole("dialog", { name: "创建节点" });
  // Dispatch at the real SVG edge / handle to avoid thin-path hit-test ambiguity.
  for (const target of [edge, page.locator(".react-flow__handle").first(), page.locator(".react-flow__controls").first()]) {
    const cancelled = await target.evaluate(el => !el.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true, button: 2 })));
    expect(cancelled).toBe(false);
    await expect(menu).toHaveCount(0);
  }
  await page.locator(".react-flow__node").first().click();
  await expect(page.locator(".react-flow__node.selected")).toHaveCount(1);
  const pane = (await page.locator(".react-flow__pane").boundingBox())!;
  await page.mouse.click(pane.x + 25, pane.y + 25, { button: "right" });
  await menu.getByRole("combobox").fill("image");
  await page.keyboard.press("Backspace");
  await expect(page.locator(".react-flow__node")).toHaveCount(2);
  await page.keyboard.press("Escape");
  // Original Shift-drag box selection remains available after closing the menu.
  const target = (await page.locator(".react-flow__node").last().boundingBox())!;
  await page.keyboard.down("Shift");
  await page.mouse.move(target.x - 10, target.y - 10);
  await page.mouse.down();
  await page.mouse.move(target.x + target.width + 10, target.y + target.height + 10, { steps: 8 });
  await page.mouse.up();
  await page.keyboard.up("Shift");
  await expect(page.locator(".react-flow__node.selected")).toHaveCount(1);
});
