import { test, expect } from "@playwright/test";

const workflowId = "11111111-1111-1111-1111-111111111111";

for (const theme of ["light", "dark"] as const) {
  test(`reference quote matches adjacent node controls in ${theme} theme`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.addInitScript(theme => {
      localStorage.setItem("hololab.theme", theme);
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
    const reference = node.getByRole("button", { name: "引用到 Flops", exact: true });
    const quote = reference.locator("svg");
    const source = node.locator("[data-hl-open-source] svg");
    await expect(source).toBeVisible();
    await expect(node.locator('button[data-tooltip="重新运行"], button[data-tooltip="运行"]')).toBeVisible();
    await expect(quote).toHaveAttribute("data-reference-icon", "quote");
    await expect(quote.locator("path")).toHaveCount(2);
    await expect(reference).not.toHaveAttribute("title");
    await expect(reference).toHaveAttribute("data-tooltip", "引用到 Flops");
    for (const attribute of ["width", "height", "stroke-width"]) {
      expect(await quote.getAttribute(attribute)).toBe(await source.getAttribute(attribute));
    }
    await expect(quote).toHaveAttribute("stroke", "currentColor");
    await page.clock.install();
    await page.clock.pauseAt(new Date());
    const tooltip = page.getByRole("tooltip");
    await source.hover();
    await page.clock.runFor(200);
    await expect(tooltip).toHaveCount(0);
    await page.clock.runFor(200);
    await expect(tooltip).toHaveText("查看代码");
    await reference.hover();
    await page.clock.runFor(450);
    await expect(tooltip).toHaveText("引用到 Flops");
    await expect(tooltip).toHaveCSS("font-size", "11px");
    await expect(reference).toHaveAttribute("aria-describedby", await tooltip.getAttribute("id") as string);
    const cardBox = (await node.boundingBox())!;
    const tipBox = (await tooltip.boundingBox())!;
    const clipTop = Math.max(0, Math.min(cardBox.y, tipBox.y) - 12);
    await page.screenshot({
      path: testInfo.outputPath(`tooltip-${theme}.png`), animations: "disabled",
      clip: { x: Math.max(0, cardBox.x - 12), y: clipTop, width: cardBox.width + 24, height: cardBox.y + cardBox.height - clipTop + 12 },
    });
    await page.keyboard.press("Escape");
    await expect(tooltip).toHaveCount(0);
    await expect(reference).not.toHaveAttribute("aria-describedby");
    await node.locator('button[data-tooltip="重新运行"], button[data-tooltip="运行"]').hover();
    await page.clock.runFor(450);
    await expect(tooltip).toHaveText(/运行/);
    await page.mouse.move(0, 0);
    await expect(tooltip).toHaveCount(0);

    // Keyboard focus shows immediately; toolbar placement flips below at the top edge.
    await node.locator("[data-hl-open-source]").focus();
    await page.keyboard.press("Tab");
    await expect(reference).toBeFocused();
    await expect(tooltip).toHaveText("引用到 Flops");
    const toolbarRef = page.getByRole("button", { name: "引用到 Flops：引用整张流程", exact: true });
    await toolbarRef.hover();
    await page.clock.runFor(450);
    const toolbarBox = (await toolbarRef.boundingBox())!;
    const flipped = (await tooltip.boundingBox())!;
    expect(flipped.y).toBeGreaterThanOrEqual(toolbarBox.y + toolbarBox.height);
    expect(flipped.x + flipped.width).toBeLessThanOrEqual(1440 - 8);
    await page.mouse.move(0, 0);
    await node.screenshot({ path: testInfo.outputPath(`reference-quote-${theme}.png`), animations: "disabled" });
    await testInfo.attach(`reference-quote-${theme}`, { path: testInfo.outputPath(`reference-quote-${theme}.png`), contentType: "image/png" });
  });
}
