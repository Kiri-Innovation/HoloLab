import { test, expect } from "./fixtures/node-creation";

test.use({ viewport: { width: 1440, height: 1000 } });

test("node context menu copies, pastes, deletes and opens package detail", async ({ page }) => {
  const pane = page.locator(".react-flow__pane");
  const bounds = (await pane.boundingBox())!;
  const point = { x: Math.round(bounds.x + 260), y: Math.round(bounds.y + 220) };
  await page.mouse.click(point.x, point.y, { button: "right" });
  await page.getByRole("dialog", { name: "创建节点" }).getByRole("option", { name: /image-resize/ }).click();
  const node = page.locator(".react-flow__node-algorithm").first();
  await expect(node).toBeVisible();
  const original = (await node.boundingBox())!;

  await page.mouse.click(original.x + 40, original.y + 40, { button: "right" });
  const menu = page.getByRole("menu", { name: "节点操作" });
  await expect(menu).toBeVisible();
  await expect(menu.getByRole("menuitem", { name: "粘贴" })).toBeDisabled();
  for (const theme of ["light", "dark"]) {
    await page.evaluate(value => { document.documentElement.dataset.theme = value; }, theme);
    await page.screenshot({ path: `test-results/node-context-menu-${theme}.png`, animations: "disabled" });
  }

  await menu.getByRole("menuitem", { name: "复制" }).click();
  await page.mouse.click(original.x + 40, original.y + 40, { button: "right" });
  await menu.getByRole("menuitem", { name: "粘贴" }).click();
  await expect(page.locator(".react-flow__node-algorithm")).toHaveCount(2);
  const copied = (await page.locator(".react-flow__node-algorithm").last().boundingBox())!;
  expect(copied.x).toBeGreaterThanOrEqual(original.x + 20);
  expect(copied.y).toBeGreaterThanOrEqual(original.y + 20);
  await page.mouse.click(copied.x + 40, copied.y + 40, { button: "right" });
  await menu.getByRole("menuitem", { name: "删除" }).click();
  await expect(page.locator(".react-flow__node-algorithm")).toHaveCount(1);

  await page.mouse.click(original.x + 40, original.y + 40, { button: "right" });
  await menu.getByRole("menuitem", { name: "在包管理器里查看" }).click();
  await expect(page).toHaveURL(/\/packs\?view=local&section=category&pack=kiri%2Fimage-resize%401\.0\.0/);
});

test("node context menu supports keyboard dismissal and strict outside dismissal", async ({ page }) => {
  const pane = page.locator(".react-flow__pane");
  const bounds = (await pane.boundingBox())!;
  await page.mouse.click(bounds.x + 200, bounds.y + 200, { button: "right" });
  await page.getByRole("dialog", { name: "创建节点" }).getByRole("option", { name: /image-resize/ }).click();
  const box = (await page.locator(".react-flow__node-algorithm").boundingBox())!;
  await page.mouse.click(box.x + 40, box.y + 40, { button: "right" });
  const menu = page.getByRole("menu", { name: "节点操作" });
  await expect(menu.getByRole("menuitem", { name: "复制" })).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(menu.getByRole("menuitem", { name: "运行" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(menu).toHaveCount(0);

  await page.mouse.click(box.x + 40, box.y + 40, { button: "right" });
  await page.mouse.click(bounds.x + bounds.width - 20, bounds.y + bounds.height - 20);
  await expect(menu).toHaveCount(0);
});
