import { test, expect } from "@playwright/test";

const workflowId = "11111111-1111-1111-1111-111111111111";

for (const theme of ["light", "dark"] as const) {
  test(`toast is a canvas-centered black pill in ${theme} theme`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.addInitScript(theme => {
      localStorage.setItem("hololab.theme", theme);
      Object.defineProperty(navigator, "clipboard", { value: { writeText: async () => {} } });
    }, theme);
    await page.routeWebSocket("**/*", () => {});
    await page.route("**/api/**", async route => {
      const path = new URL(route.request().url()).pathname;
      let body: unknown = [];
      // The clock-driven dismissal checks also advance the draft autosave.
      if (path === "/api/workflows" && route.request().method() === "POST") {
        body = { workflow_id: workflowId, name: "Toast placement · workflow", updated_ts: 1700000001 };
      }
      if (path === "/api/pack-catalog") body = [{
        name: "demo-echo", version: "0.1.0", manifest_hash: "demo", node_ids: [],
        description: "Example step", category: [], inputs: {}, outputs: {}, params: {}, arrayable: false,
      }];
      if (path === `/api/workflows/${workflowId}`) body = {
        workflow_id: workflowId, name: "Toast placement · workflow", created_ts: 1700000000, updated_ts: 1700000000,
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
    await page.clock.install();
    await page.clock.pauseAt(new Date());
    await page.getByRole("button", { name: "复制引用给 AI：引用整张流程", exact: true }).click();
    const toast = page.locator(".hl-toast-pill");
    await expect(toast).toHaveText("已复制引用✓");
    await expect(toast).toHaveCSS("background-color", "rgb(0, 0, 0)");
    await expect(toast).toHaveCSS("color", "rgb(255, 255, 255)");
    await expect(toast).toHaveCSS("border-radius", "999px");
    const dismiss = toast.getByRole("button", { name: "关闭提示", exact: true });
    await expect(dismiss).toHaveText("✓");
    const closeBox = (await dismiss.boundingBox())!;
    expect(closeBox.width).toBeGreaterThanOrEqual(32);
    expect(closeBox.height).toBeGreaterThanOrEqual(32);
    const anchor = (await page.locator("[data-hl-toast-anchor]").boundingBox())!;
    const before = (await toast.boundingBox())!;
    expect(Math.abs(before.x + before.width / 2 - anchor.x - anchor.width / 2)).toBeLessThan(1);
    expect(before.y - anchor.y).toBe(12);
    expect(anchor.x).toBeGreaterThan(100);
    expect(anchor.width).toBeLessThan(1440 - 200);
    expect(await page.locator(".react-flow__viewport [data-hl-toast-anchor]").count()).toBe(0);

    const graph = page.locator(".react-flow__viewport");
    const originalTransform = await graph.getAttribute("style");
    await page.mouse.move(anchor.x + anchor.width / 2, anchor.y + anchor.height / 2);
    await page.mouse.wheel(0, 180);
    await page.clock.runFor(250);
    await expect.poll(() => graph.getAttribute("style")).not.toBe(originalTransform);
    const after = (await toast.boundingBox())!;
    expect(after.x).toBeCloseTo(before.x, 1);
    expect(after.y).toBeCloseTo(before.y, 1);

    const zoomedTransform = await graph.getAttribute("style");
    await page.mouse.move(anchor.x + anchor.width / 2, anchor.y + anchor.height - 80);
    await page.mouse.down();
    await page.mouse.move(anchor.x + anchor.width / 2 + 50, anchor.y + anchor.height - 110, { steps: 5 });
    await page.mouse.up();
    await expect.poll(() => graph.getAttribute("style")).not.toBe(zoomedTransform);
    const panned = (await toast.boundingBox())!;
    expect(panned.x).toBeCloseTo(before.x, 1);
    expect(panned.y).toBeCloseTo(before.y, 1);

    await page.screenshot({ path: testInfo.outputPath(`toast-${theme}.png`), animations: "disabled" });
    await testInfo.attach(`toast-${theme}`, { path: testInfo.outputPath(`toast-${theme}.png`), contentType: "image/png" });
    await page.clock.fastForward(3500);
    await page.clock.runFor(150);
    await expect(toast).toHaveCount(0);

    // Native button activation supports mouse, Enter and Space.
    for (const activation of ["click", "Enter", "Space"]) {
      await page.getByRole("button", { name: "复制引用给 AI：引用整张流程", exact: true }).click();
      await expect(dismiss).toBeVisible();
      if (activation === "click") {
        await dismiss.hover();
        await page.clock.runFor(150);
        await expect(dismiss).toHaveCSS("cursor", "pointer");
        await expect(dismiss).toHaveCSS("background-color", "rgba(255, 255, 255, 0.15)");
        await dismiss.click();
      } else {
        await dismiss.focus();
        await dismiss.press(activation);
      }
      await page.clock.runFor(150);
      await expect(toast).toHaveCount(0);
    }

    // Failure retains retry; its checkmark only dismisses, never retries.
    await page.evaluate(() => {
      navigator.clipboard.writeText = async () => { throw new Error("denied"); };
      document.execCommand = () => false;
    });
    await page.getByRole("button", { name: "复制引用给 AI：引用整张流程", exact: true }).click();
    await expect(toast).toContainText("复制失败");
    await expect(toast.getByRole("button", { name: "重试复制" })).toBeVisible();
    await expect(dismiss).toHaveText("✓");
    await dismiss.click();
    await page.clock.runFor(150);
    await expect(toast).toHaveCount(0);

  });
}
