import { test, expect } from "@playwright/test";

const workflowId = "11111111-1111-1111-1111-111111111111";

for (const theme of ["light", "dark"] as const) {
  test(`reference outcomes use toast without expanding nodes in ${theme} theme`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.addInitScript(theme => {
      localStorage.setItem("hololab.theme", theme);
      Object.defineProperty(navigator, "clipboard", { value: { writeText: async () => {} } });
      Object.defineProperty(window, "flops", { value: {
        version: 1,
        showDocument: async () => ({ success: true }),
        insertReference: async () => ({ success: true }),
      } });
    }, theme);
    await page.routeWebSocket("**/*", () => {});
    await page.route("**/api/**", async route => {
      const path = new URL(route.request().url()).pathname;
      let body: unknown = [];
      // Focus/hover checks outlive the draft autosave debounce.
      if (path === "/api/workflows" && route.request().method() === "POST") {
        body = { workflow_id: workflowId, name: "Reference icon · workflow", updated_ts: 1700000001 };
      }
      if (path === "/api/pack-catalog") body = [{
        name: "demo-echo", version: "0.1.0", manifest_hash: "demo", node_ids: [],
        description: "Example step", category: [], inputs: {}, outputs: {}, params: {}, arrayable: false,
      }];
      if (path === `/api/workflows/${workflowId}`) body = {
        workflow_id: workflowId, name: "Reference icon · workflow", created_ts: 1700000000, updated_ts: 1700000000,
        graph: {
          nodes: [0, 1].map(i => ({ id: `step-${i}`, algorithm_name: "demo-echo", algorithm_version: "0.1.0", position: { x: i * 330, y: i * 130 }, params: {}, assigned_node_id: null })),
          edges: [],
        },
      };
      if (path === "/api/nodes/metrics/history") body = { sample_interval_s: 5, nodes: {} };
      if (path === "/api/artifacts/summary") body = { total_bytes: 0, exclusive_bytes: 0, artifact_count: 0 };
      await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
    });
    await page.goto(`/w/${workflowId}`);
    await expect(page.locator(".react-flow__node")).toHaveCount(2);
    await expect(page.locator("[data-hl-toast-anchor]")).toHaveCount(1);
    const node = page.locator(".react-flow__node").first();
    const button = node.getByRole("button", { name: "引用到 Flops", exact: true });
    const toast = page.locator(".hl-toast-pill");
    const before = (await node.boundingBox())!;
    await page.clock.install();
    await page.clock.pauseAt(new Date());
    await button.click();
    await expect(toast).toHaveAttribute("role", "status");
    await expect(toast).toContainText("已插入 Flops 输入框（1 条）");
    await expect(button).toHaveText("✓");
    await expect(node.getByText("已插入", { exact: false })).toHaveCount(0);
    expect((await node.boundingBox())!.width).toBeCloseTo(before.width, 2);
    await expect(button).toHaveAttribute("aria-label", "引用到 Flops");
    await expect(button).toHaveAttribute("aria-description", "已插入 Flops 输入框（1 条）");
    await page.clock.runFor(150);
    await page.screenshot({ path: testInfo.outputPath(`insert-success-${theme}.png`) });
    await page.clock.runFor(1900);
    await expect(button.locator('[data-reference-icon="quote"]')).toBeVisible();
    await expect(toast).toBeVisible();
    await page.clock.runFor(1600);
    await expect(toast).toHaveCount(0);

    await page.evaluate(() => {
      Object.assign(window.flops!, { insertReference: async () => ({ success: false, reason: "Flops 输入框暂不可用" }) });
    });
    await button.click();
    await expect(toast).toHaveAttribute("role", "alert");
    await expect(toast).toContainText("Flops 输入框暂不可用");
    await expect(toast).toContainText("已复制，可粘贴到对话");
    await expect(button).toHaveText("!");
    await expect(button).toHaveCSS("color", await button.evaluate(el => getComputedStyle(el).getPropertyValue("--error").trim()).then(value => {
      // Compare resolved CSS colors without depending on theme token syntax.
      return button.evaluate((el, value) => { const probe = document.createElement("span"); probe.style.color = value; el.appendChild(probe); const color = getComputedStyle(probe).color; probe.remove(); return color; }, value);
    }));
    await expect(node.getByText("Flops 输入框暂不可用", { exact: false })).toHaveCount(0);
    expect((await node.boundingBox())!.width).toBeCloseTo(before.width, 2);
    await page.clock.runFor(150);
    await page.screenshot({ path: testInfo.outputPath(`insert-fallback-${theme}.png`) });
    await page.clock.runFor(1900);
    await expect(button.locator('[data-reference-icon="quote"]')).toBeVisible();
    await page.clock.runFor(5700);
    await expect(toast).toBeVisible();
    await page.clock.runFor(450);
    await expect(toast).toHaveCount(0);
  });
}
