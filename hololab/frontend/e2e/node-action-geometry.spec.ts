import { test, expect } from "@playwright/test";
import { writeFile } from "node:fs/promises";

const workflowId = "11111111-1111-1111-1111-111111111111";

for (const theme of ["light", "dark"] as const) {
  test(`node action geometry in ${theme} theme`, async ({ page }, testInfo) => {
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
    await page.locator(".react-flow__viewport").evaluate(el => {
      (el as HTMLElement).style.transform = "translate(160px, 180px) scale(1)";
    });
    const node = page.locator(".react-flow__node").first();
    const buttons = [node.locator("[data-hl-open-source]"), node.getByRole("button", { name: "引用到 Flops", exact: true }), node.getByRole("button", { name: /^(重新运行|运行)$/ })];
    const measurements = [];
    for (const button of buttons) {
      measurements.push(await button.evaluate(el => {
        const r = el.getBoundingClientRect(), c = getComputedStyle(el), svg = el.querySelector("svg");
        return { label: el.getAttribute("aria-label"), x:r.x, y:r.y, width:r.width, height:r.height,
          radius:c.borderRadius, border:c.borderWidth, padding:c.padding, boxSizing:c.boxSizing, fontSize:c.fontSize,
          icon: svg ? {width:svg.getAttribute("width"),height:svg.getAttribute("height"),viewBox:svg.getAttribute("viewBox"),stroke:svg.getAttribute("stroke-width")} : {text:el.textContent} };
      }));
    }
    await writeFile(testInfo.outputPath(`geometry-${theme}.json`), JSON.stringify(measurements, null, 2));
    const x=Math.min(...measurements.map(m=>m.x))-8, y=Math.min(...measurements.map(m=>m.y))-8;
    const right=Math.max(...measurements.map(m=>m.x+m.width))+8, bottom=Math.max(...measurements.map(m=>m.y+m.height))+8;
    await page.screenshot({path:testInfo.outputPath(`buttons-${theme}.png`),clip:{x,y,width:right-x,height:bottom-y}});
    if (!process.env.BUTTON_BASELINE) {
      for(const m of measurements) {
        expect(m.width).toBe(24); expect(m.height).toBe(24);
        expect(m.radius).toBe("4px"); expect(m.border).toBe("1px"); expect(m.padding).toBe("0px");
        expect(m.icon).toMatchObject({width:"12",height:"12",viewBox:"0 0 24 24",stroke:"2.2"});
      }
    }
  });
}
