import { test, expect } from "@playwright/test";

const workflowId = "11111111-1111-1111-1111-111111111111";

for (const theme of ["light", "dark"] as const) {
  test(`tooltip zoom and arrow placement in ${theme} theme`, async ({ page }, testInfo) => {
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
    const reference = page.locator('.react-flow__node[data-id="step-0"]').getByRole("button", { name: "引用到 Flops", exact: true });
    const tooltip = page.getByRole("tooltip");
    await page.clock.install();
    await page.clock.pauseAt(new Date());
    async function checkTail() {
      const result = await tooltip.evaluate(el => {
        const box = el.getBoundingClientRect();
        const style = getComputedStyle(el, "::after");
        const target = document.querySelector(`[aria-describedby="${el.id}"]`)!.getBoundingClientRect();
        return { tipX: box.left + (el as HTMLElement).clientLeft + parseFloat(style.left), center: target.left + target.width / 2, placement: (el as HTMLElement).dataset.placement,
          down: parseFloat(style.borderTopWidth), up: parseFloat(style.borderBottomWidth) };
      });
      expect(result.tipX).toBeCloseTo(result.center, 1);
      expect(result.placement === "above" ? result.down : result.up).toBeGreaterThan(0);
    }
    async function capture(name: string) {
      const clip = await tooltip.evaluate(el => {
        const target = document.querySelector(`[aria-describedby="${el.id}"]`)!;
        const anchor = (target.closest(".react-flow__node") || target).getBoundingClientRect();
        const tip = el.getBoundingClientRect();
        const x = Math.max(0, Math.min(anchor.left, tip.left) - 16);
        const y = Math.max(0, Math.min(anchor.top, tip.top) - 16);
        return { x, y, width: Math.min(innerWidth, Math.max(anchor.right, tip.right) + 16) - x,
          height: Math.min(innerHeight, Math.max(anchor.bottom, tip.bottom) + 16) - y };
      });
      await page.screenshot({ path: testInfo.outputPath(`${name}-${theme}.png`), clip, animations: "disabled" });
    }
    for (const zoom of [0.5, 1, 1.5]) {
      await page.mouse.move(0, 0);
      await page.locator(".react-flow__viewport").evaluate((el, zoom) => {
        (el as HTMLElement).style.transform = `translate(160px, 180px) scale(${zoom})`;
      }, zoom);
      await reference.hover();
      await page.clock.runFor(450);
      await expect(tooltip).toBeVisible();
      expect(await tooltip.evaluate(el => parseFloat(getComputedStyle(el).fontSize))).toBeCloseTo(11 * zoom, 2);
      await expect(tooltip).toHaveAttribute("data-placement", "above");
      await checkTail();
      await capture(`zoom-${zoom * 100}`);
    }
    // Keep keyboard focus active: zoom can legitimately move a hovered button
    // away from the pointer, which should dismiss a hover tooltip.
    await page.mouse.move(0, 0);
    await page.keyboard.press("Tab");
    await reference.focus();
    await expect(tooltip).toBeVisible();
    // A focused tooltip updates after a transform change, without re-hover.
    await page.locator(".react-flow__viewport").evaluate(el => {
      (el as HTMLElement).style.transform = "translate(160px, 180px) scale(1)";
    });
    await page.clock.runFor(50);
    await expect(tooltip).toHaveCSS("font-size", "11px");
    await checkTail();

    const toolbar = page.getByRole("button", { name: "引用到 Flops：引用整张流程", exact: true });
    await toolbar.hover();
    await page.clock.runFor(450);
    await expect(tooltip).toHaveCSS("font-size", "11px");
    await expect(tooltip).toHaveAttribute("data-placement", "below");
    await checkTail();
    await capture("below");

    // Place the real toolbar action against either browser edge, as a compact
    // icon-only control, to exercise clamping independently of dock widths.
    for (const side of ["left", "right"] as const) {
      await page.mouse.move(700, 900);
      await toolbar.evaluate((el, side) => {
        const button = el as HTMLButtonElement;
        Object.assign(button.style, { position: "fixed", top: "200px", left: side === "left" ? "0" : "auto", right: side === "right" ? "0" : "auto", width: "24px", padding: "0", zIndex: "13000" });
        button.querySelectorAll("span").forEach(span => { span.style.display = "none"; });
      }, side);
      await toolbar.hover();
      await page.clock.runFor(450);
      await checkTail();
      const box = (await tooltip.boundingBox())!;
      expect(box.x).toBeGreaterThanOrEqual(8);
      expect(box.x + box.width).toBeLessThanOrEqual(1432);
      await capture(`edge-${side}`);
    }
  });
}
