import { test, expect } from "@playwright/test";

const workflowId = "11111111-1111-1111-1111-111111111111";

for (const theme of ["light", "dark"] as const) {
  test(`independent controls share tooltips in ${theme} theme`, async ({ page }, testInfo) => {
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
    const tooltip = page.getByRole("tooltip");
    async function show(selector: string, label: string, screenshot?: string) {
      const button = page.locator(selector).first();
      await expect(button).not.toHaveAttribute("title");
      await button.hover();
      await expect(tooltip).toHaveText(label);
      await expect(tooltip).toHaveCSS("font-size", "11px");
      if (screenshot) {
        const a = (await button.boundingBox())!, b = (await tooltip.boundingBox())!;
        const x = Math.max(0, Math.min(a.x, b.x) - 60), y = Math.max(0, Math.min(a.y, b.y) - 40);
        await page.screenshot({ path: testInfo.outputPath(`${screenshot}-${theme}.png`),
          clip: { x, y, width: Math.min(1440 - x, Math.max(a.x + a.width, b.x + b.width) - x + 60),
            height: Math.min(1000 - y, Math.max(a.y + a.height, b.y + b.height) - y + 40) } });
      }
      return button;
    }
    const themeLabel = theme === "light" ? "跟随系统主题" : "切换浅色主题";
    await show(`button[data-tooltip="${themeLabel}"]`, themeLabel, "theme");
    const minimap = await show('button[data-tooltip="显示小地图"]', "显示小地图", "minimap");
    await minimap.click();
    await expect(page.locator('button[data-tooltip="隐藏小地图"]')).toBeVisible();
    await page.mouse.move(0, 0);
    await show('button[data-tooltip="隐藏小地图"]', "隐藏小地图");
    await show('button[data-tooltip="刷新"]', "刷新", "refresh");
    await show('.react-flow__controls-zoomin', "放大画布");
    await show('.react-flow__controls-zoomout', "缩小画布");
    await show('.react-flow__controls-fitview', "适应视图");
    const lock = await show('.react-flow__controls-interactive', "锁定节点交互");
    // Keyboard activation retains focus: the visible text must update in place.
    await page.mouse.move(0, 0);
    await page.keyboard.press("Tab");
    await lock.focus();
    await expect(tooltip).toHaveText("锁定节点交互");
    await page.keyboard.press("Enter");
    await expect(tooltip).toHaveText("解锁节点交互");
    await expect(lock).toHaveAttribute("aria-label", "解锁节点交互");
    await expect(lock).not.toHaveAttribute("title");
    await page.keyboard.press("Escape");
    await expect(tooltip).toHaveCount(0);
  });
}

test("player tooltip follows play, pause and ended without re-hover", async ({ page }) => {
  // Mount the real preview component with deterministic media events; no decoder/network dependency.
  await page.route("**/tooltip-preview-fixture", route => route.fulfill({ contentType: "text/html", body: `
    <html><head><script type="module" src="/@vite/client"></script></head><body>
    <div id="fixture" style="margin:100px;width:500px"></div>
    <script type="module">
      import RefreshRuntime from '/@react-refresh';
      RefreshRuntime.injectIntoGlobalHook(window);
      window.$RefreshReg$ = () => {};
      window.$RefreshSig$ = () => type => type;
      window.__vite_plugin_react_preamble_installed__ = true;
      const { default: React } = await import('/node_modules/.vite/deps/react.js');
      import ReactDOM from '/node_modules/.vite/deps/react-dom_client.js';
      const { Preview } = await import('/src/canvas/previews.tsx');
      const { TooltipLayer } = await import('/src/ui/TooltipLayer.tsx');
      import '/src/styles.css';
      ReactDOM.createRoot(document.getElementById('fixture')).render(React.createElement(React.Fragment, null,
        React.createElement(Preview, { spec: {}, baseUrl: '/proxy/demo/videos', storage: 'dir',
          handleId: 'videos', tags: ['rig_capture'] }), React.createElement(TooltipLayer)));
    </script></body></html>` }));
  await page.route("**/api/handles/videos/summary", route => route.fulfill({ json: {
    fields: { entries: [{ name: "camera.mp4", is_dir: false, size_bytes: 1 }] },
  } }));
  await page.route("**/proxy/**", route => route.fulfill({ status: 204 }));
  await page.goto("/tooltip-preview-fixture");
  const video = page.locator("video").first();
  await expect(video).toBeAttached();
  await video.evaluate(el => {
    Object.defineProperty(el, "duration", { value: 10 });
    el.play = async () => {};
    el.pause = () => {};
    el.dispatchEvent(new Event("loadedmetadata"));
  });
  const play = page.locator("[data-hl-play]");
  const tooltip = page.getByRole("tooltip");
  await expect(play).toBeEnabled();
  await play.hover();
  await expect(tooltip).toHaveText("播放");
  await expect(play).not.toHaveAttribute("title");
  await play.click();
  await expect(play).toHaveAttribute("data-tooltip", "暂停");
  await page.mouse.move(0, 0);
  await play.hover();
  await expect(tooltip).toHaveText("暂停");
  await video.evaluate(el => {
    Object.defineProperty(el, "currentTime", { configurable: true, value: 10 });
    el.dispatchEvent(new Event("timeupdate"));
    el.dispatchEvent(new Event("ended"));
  });
  await expect(tooltip).toHaveText("重新播放");
  await expect(play).toHaveAttribute("aria-label", "重新播放");
});
