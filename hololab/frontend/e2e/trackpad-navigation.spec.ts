import { expect, test, type Page } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";

test.use({ viewport: { width: 1700, height: 1100 } });

async function openWorkflow(page: Page) {
  const output = (tags: string[], arrayed = false) => ({ tags, arrayed, dim_labels: arrayed ? ["frame"] : [], preview: null });
  const pack = (name: string, outputs = {}, inputs = {}, params = {}) => ({ name, version: "1", manifest_hash: "wheel", node_ids: ["device"], outputs, inputs, params });
  const packs = [
    pack("wheel-images", { images: output(["image"], true) }, {}, {
      count: { type: "int", default: 17, pinned: true, optional: false },
      use_gpu: { type: "bool", default: true, pinned: true, optional: false },
      refine_intrinsics: { type: "bool", default: false, pinned: true, optional: false },
      sift_max_num_feat: { type: "int", default: 8192, pinned: true, optional: false },
    }),
    pack("wheel-sink", {}, { images: output(["image"], true), value: { ...output(["float"]), storage: "file", scalar: true, optional: true, inline_default: 1 } }),
    pack("kiri/image-crop", { image: output(["image"]) }),
    pack("kiri/compose-video", { video: output(["video"]) }),
    pack("wheel-text", { text: { ...output(["text"]), preview: { viewer: "text", member: null } } }),
    pack("wheel-colmap", { scene: output(["colmap4d-cams"]) }),
    { ...pack("wheel-custom"), hide_default_output_previews: true,
      node_viewers: [{ id: "custom", title: "Custom viewer", renderer: "iframe", entry: "viewer.html", default: true }] },
    pack("wheel-lagsplat", { model: { ...output(["lagsplat"]), preview: { viewer: "lagsplat", member: null } } }),
    pack("wheel-colmap3d", { scene: output(["colmap-cams"]) }),
  ];
  const ids = ["images", "sink", "crop", "compose", "text", "colmap", "custom", "lagsplat", "colmap3d"];
  const graph = {
    nodes: [...packs.map((p, i) => ({ id: ids[i], algorithm_name: p.name, algorithm_version: "1", assigned_node_id: "device",
      params: i === 0 ? { count: 17 } : {}, preview_open: Object.keys(p.outputs)[0] ?? null,
      position: { x: (i % 3) * 600, y: Math.floor(i / 3) * 750 } })),
      { id: "pinned-list", kind: "intrinsic", operation: "constant-float-array", algorithm_name: "@builtin/constant-float-array",
        algorithm_version: "1", params: { items: Array(24).fill(17) }, position: { x: 1800, y: 0 } }],
    edges: [{ id: "wire", source: "images", sourceHandle: "images", target: "sink", targetHandle: "images" }],
  };
  const jobs = [0, 4, 5, 7, 8].map(i => ({ job_id: `job-${ids[i]}`, graph_node_id: ids[i], node_id: "device", workflow_id: "wheel-matrix",
    algorithm_name: packs[i].name, algorithm_version: "1", state: "done", params: {}, input_handles: {},
    output_handles: { [Object.keys(packs[i].outputs)[0]]: ids[i] }, created_ts: 1 }));
  await page.routeWebSocket("**", () => {});
  await page.addInitScript(() => { delete (window as { IntersectionObserver?: unknown }).IntersectionObserver; });
  const frame = '<body style="margin:0;height:100vh"><div id="wheels">0</div><script>let count=0;addEventListener("wheel",e=>{e.preventDefault();document.querySelector("#wheels").textContent=String(++count)},{passive:false});parent.postMessage({type:"colmap-ready"},location.origin)</script></body>';
  for (const url of ["**/colmaputil/index.html?*", "**/lagsplat-viewer.html", "**/pack-assets/**"]) {
    await page.route(url, route => route.fulfill({ contentType: "text/html", body: frame }));
  }
  await page.route("**/proxy/device/**", route => {
    if (route.request().url().includes("/text")) return route.fulfill({ body: Array.from({ length: 150 }, (_, i) => `Diagnostic line ${i}`).join("\n") });
    if (route.request().url().includes("/_format/colmap4d/")) return route.fulfill({ json: {
      format: "colmap4d", part: "cameras", result: { files: [{ name: "cameras.txt", base64: "IyBlbXB0eQ==" }],
        total: 0, shown: 0, image_names: [], estimated: false, camera_bounds: null, next_offset: null },
    } });
    if (route.request().url().includes("/colmap3d")) return route.fulfill({ body: "# empty COLMAP fixture\n" });
    return route.fulfill({ status: 404 });
  });
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = [];
    if (path === "/api/pack-catalog") body = packs;
    else if (path === "/api/nodes/metrics/history") body = { nodes: {}, sample_interval_s: 3 };
    else if (path === "/api/workflows/wheel-matrix") body = { workflow_id: "wheel-matrix", name: "Wheel ownership matrix", graph, created_ts: 1, updated_ts: 1 };
    else if (path === "/api/workflows/wheel-matrix/canvas-state") body = { canvas_jobs: jobs, staleness: {} };
    else if (path === "/api/handles/images/summary") body = { fields: { entries: Array.from({ length: 80 }, (_, i) => ({ name: `image-${i}.jpg`, is_dir: false })) } };
    else if (["images", "text", "colmap", "lagsplat", "colmap3d"].some(id => path === `/api/handles/${id}`)) {
      const id = path.split("/").at(-1)!;
      body = { handle_id: id, node_id: "device", storage: id === "text" ? "file" : "dir", tags: [id === "images" ? "image" : id === "colmap" ? "colmap4d-cams" : id === "colmap3d" ? "colmap-cams" : id],
        proxy_url: `/proxy/device/${id}`, absolute_path: `/${id}`, deleted_ts: null, preview: ["text", "lagsplat"].includes(id) ? { viewer: id, member: null } : null,
        dim_labels: id === "images" ? ["frame"] : [], dim_sizes: id === "images" ? [80] : [] };
    }
    await route.fulfill({ json: body });
  });
  await page.goto("/w/wheel-matrix");
  await expect(page.locator('[data-hl-frame-count="80"]')).toBeAttached();
  await expect(page.locator(".react-flow__edge .react-flow__edge-path")).toBeAttached();
  await expect(page.locator('.react-flow__node[data-id="text"] pre')).toContainText("Diagnostic line 149");
  await page.getByRole("button", { name: "Show minimap" }).click();
}

type WheelTarget = readonly [name: string, selector: string, owner: "canvas" | "local" | "parameters" | "chrome" | "focused-frame", activateNode?: string];
const rows: readonly WheelTarget[] = [
  ["background", ".react-flow__pane", "canvas"],
  ["viewport", ".react-flow__viewport", "canvas"],
  ["node wrapper", '.react-flow__node[data-id="images"]', "canvas"],
  ["node header", '.react-flow__node[data-id="images"] [data-hl-node-title]', "canvas"],
  ["node footer/button", '.react-flow__node[data-id="images"] [data-hl-node-footer] button', "canvas"],
  ["port text", '.react-flow__node[data-id="images"] [data-tooltip-type]', "canvas"],
  ["handle", ".react-flow__handle", "canvas"],
  ["edge SVG path", ".react-flow__edge-path", "canvas"],
  ["edge wrapper", ".react-flow__edge", "canvas"],
  ["edge hit path", ".react-flow__edge-interaction", "canvas"],
  ["edge label", ".hl-edge-chip", "canvas"],
  ["edge label portal", ".react-flow__edgelabel-renderer", "canvas"],
  ["drawer", '.react-flow__node[data-id="images"] .hl-preview-drawer', "canvas"],
  ["image tile", "[data-hl-frame-card]", "canvas"],
  ["image", "[data-hl-frame-card] img", "canvas"],
  ["inactive diagnostic", '.react-flow__node[data-id="text"] pre', "canvas"],
  ["active diagnostic", '.react-flow__node[data-id="text"] pre', "local", "text"],
  ["inactive custom viewer shield", '.react-flow__node[data-id="custom"] [data-viewer-interaction] > div', "canvas"],
  ["active custom viewer host", '.react-flow__node[data-id="custom"] [data-viewer-interaction]', "focused-frame", "custom"],
  ["inactive LaGSplat shield", '.react-flow__node[data-id="lagsplat"] [data-viewer-interaction] > div', "canvas"],
  ["active LaGSplat host", '.react-flow__node[data-id="lagsplat"] [data-viewer-interaction]', "focused-frame", "lagsplat"],
  ["inactive COLMAP3D shield", '.react-flow__node[data-id="colmap3d"] [data-viewer-interaction] > div', "canvas"],
  ["active COLMAP3D host", '.react-flow__node[data-id="colmap3d"] [data-viewer-interaction]', "focused-frame", "colmap3d"],
  ["inline parameter", ".hl-inline-port-card", "local"],
  ["node-card parameters", '.react-flow__node[data-id="images"] .hl-pinned-parameters', "canvas"],
  ["node-card checkbox", '.react-flow__node[data-id="images"] input[aria-label="use_gpu"]', "canvas"],
  ["node-card number", '.react-flow__node[data-id="images"] input[aria-label="count"]', "canvas"],
  ["pinned panel", '.react-flow__node[data-id="pinned-list"] .hl-pinned-parameters', "parameters"],
  ["pinned number", '.react-flow__node[data-id="pinned-list"] .hl-pinned-parameters input[type="number"]', "parameters"],
  ["crop editor", ".hl-image-crop-editor", "local"],
  ["composition editor", ".hl-compose-editor", "local"],
  ["COLMAP viewer", '.react-flow__node[data-id="colmap"] .nowheel', "local"],
  ["controls", ".react-flow__controls", "chrome"],
  ["controls button", ".react-flow__controls-fitview svg", "chrome"],
  ["minimap", ".react-flow__minimap", "chrome"],
  ["minimap SVG", ".react-flow__minimap-svg", "chrome"],
  ["minimap node", ".react-flow__minimap-node", "chrome"],
  ["toggle panel", '.react-flow__panel:has(button[aria-label="Hide minimap"])', "chrome"],
] as const;

test("wheel ownership matrix on a real workflow", async ({ page }, info) => {
  test.setTimeout(120_000);
  await openWorkflow(page);
  const viewport = page.locator(".react-flow__viewport");
  const transform = () => viewport.evaluate(el => {
    const m = new DOMMatrixReadOnly(getComputedStyle(el).transform);
    return { x: m.m41, y: m.m42, scale: m.a };
  });
  const mode = page.locator(".hl-trackpad-navigation");
  const results: { name: string; enabled: boolean; ctrlKey: boolean; decision: string; behavior: string; expected: string; before: unknown; after: unknown }[] = [];

  for (const enabled of [false, true]) {
    if (await mode.getAttribute("aria-pressed") !== String(enabled)) await mode.click();
    // Inventory every rendered element, using the actual production helper.
    const inventory = await page.locator(".react-flow").evaluate(async (root, enabled) => {
      const modulePath = "/src/canvas/trackpadNavigation.ts";
      const { keepsExistingWheelBehavior, shouldPanWheel } = await import(modulePath);
      return [...root.querySelectorAll("*")].map(el => ({ tag: el.tagName, classes: el.getAttribute("class"),
        owner: el.closest("[data-wheel-owner]")?.getAttribute("data-wheel-owner") ?? null,
        decision: keepsExistingWheelBehavior(el) ? "exempt" : shouldPanWheel(enabled, { ctrlKey: false }) ? "pan" : "zoom",
        scrollHeight: el.scrollHeight, clientHeight: el.clientHeight, overflowY: getComputedStyle(el).overflowY,
      }));
    }, enabled);
    mkdirSync("/tmp/hololab-wheel-matrix", { recursive: true });
    writeFileSync(`/tmp/hololab-wheel-matrix/inventory-${enabled}.json`, JSON.stringify(inventory, null, 2));
    // Every rendered element must have the same decision as its explicit owner;
    // no framework class or ancestor combination may create a hidden exception.
    if (enabled) for (const element of inventory) expect(element.decision).toBe(element.owner === "local" ? "exempt" : "pan");
    await info.attach(`element-inventory-${enabled}`, { body: JSON.stringify(inventory, null, 2), contentType: "application/json" });

    for (const ctrlKey of [false, true]) {
      for (const [name, selector, owner, activateNode] of rows) {
        const target = page.locator(selector).first();
        const gate = page.locator(`.react-flow__node[data-id="${activateNode}"] [data-viewer-interaction]`);
        if (activateNode) {
          await gate.locator(':scope > div').last().dispatchEvent("click");
          await expect(gate).toHaveAttribute("data-wheel-owner", "local");
        }
        await expect(target, name).toBeAttached();
        await page.waitForTimeout(170); // Let d3 finish its previous wheel gesture.
        const before = await transform();
        const decision = await target.evaluate(async el => {
          const modulePath = "/src/canvas/trackpadNavigation.ts";
          return (await import(modulePath)).keepsExistingWheelBehavior(el) ? "exempt" : "canvas";
        });
        // Zoom toward 1 so long matrices cannot hit min/max zoom and create a
        // false "local" result. Panning still exercises both signs of deltaY.
        await target.evaluate((el, init) => el.dispatchEvent(new WheelEvent("wheel", {
          bubbles: true, cancelable: true, deltaX: 4, ...init, clientX: 650, clientY: 400,
        })), { ctrlKey, deltaY: before.scale < 1 ? -5 : 5 });
        await page.waitForTimeout(30);
        const after = await transform();
        const behavior = after.scale !== before.scale ? "zoom" : after.x !== before.x || after.y !== before.y ? "pan" : "local";
        const expected = owner === "canvas" ? enabled && !ctrlKey ? "pan" : "zoom"
          : owner === "parameters" ? enabled && !ctrlKey ? "local" : "zoom"
          : owner === "focused-frame" ? enabled ? "local" : "zoom" : "local";
        results.push({ name, enabled, ctrlKey, decision, behavior, expected, before, after });
        expect(behavior, `${name}, trackpad=${enabled}, ctrl=${ctrlKey}`).toBe(expected);
        await expect(mode).toHaveAttribute("aria-pressed", String(enabled));
        if (activateNode) {
          await page.locator("body").dispatchEvent("mousedown");
          await expect(gate).toHaveAttribute("data-viewer-interaction", "inactive");
        }
      }
    }
  }
  mkdirSync("/tmp/hololab-wheel-matrix", { recursive: true });
  writeFileSync("/tmp/hololab-wheel-matrix/after.json", JSON.stringify(results, null, 2));
  console.log(`Wheel matrix: ${rows.length} targets × 2 modes × 2 Ctrl states = ${results.length} passing cells`);
  await info.attach("wheel-matrix", { body: JSON.stringify(results, null, 2), contentType: "application/json" });

  // Synthetic wheel verifies routing; native wheel verifies real local scrolling.
  // Fit first so the browser can hit the actual target after the navigation matrix.
  const frameNode = async (id: string) => {
    await page.locator(`.react-flow__node[data-id="${id}"] [data-hl-node-title]`).dispatchEvent("click");
    await page.locator("body").press("f");
    await page.waitForTimeout(300);
  };
  for (const [name, selector] of [
    ["parameters", '.react-flow__node[data-id="pinned-list"] .hl-pinned-parameters'],
    ["diagnostic", '.react-flow__node[data-id="text"] [data-viewer-interaction]'],
  ]) {
    const host = page.locator(selector);
    await frameNode(name === "parameters" ? "pinned-list" : "text");
    if (name === "diagnostic") {
      await host.locator(':scope > div').last().dispatchEvent("click");
      await expect(host).toHaveAttribute("data-wheel-owner", "local");
    }
    const scroller = name === "parameters" ? host : host.locator(':scope > div').first();
    expect(await scroller.evaluate(el => el.scrollHeight > el.clientHeight), name).toBe(true);
    await scroller.evaluate(el => { el.scrollTop = 0; });
    await host.hover();
    const before = await transform();
    await page.mouse.wheel(0, 80);
    await expect.poll(() => scroller.evaluate(el => el.scrollTop)).toBeGreaterThan(0);
    expect(await transform(), name).toEqual(before);
    await scroller.evaluate(el => { el.scrollTop = el.scrollHeight; });
    await page.mouse.wheel(0, 80);
    await page.waitForTimeout(180);
    expect(await transform(), `${name} at scroll boundary`).toEqual(before);
    if (name === "parameters") await expect(host.getByRole("spinbutton").first()).toHaveValue("17");
  }

  // Real iframe events stay within the viewer document. Activation declares
  // ownership only while focused; inactive shields remain canvas targets above.
  for (const id of ["custom", "lagsplat", "colmap3d", "colmap"]) {
    await frameNode(id);
    const node = page.locator(`.react-flow__node[data-id="${id}"]`);
    const gate = node.locator("[data-viewer-interaction]");
    await expect(gate).toHaveAttribute("data-viewer-interaction", "inactive");
    await gate.locator(':scope > div').last().dispatchEvent("click");
    await expect(gate).toHaveAttribute("data-viewer-interaction", "active");
    const iframe = gate.locator("iframe");
    const counter = iframe.contentFrame().locator("#wheels");
    const count = await counter.innerText();
    await iframe.hover();
    const before = await transform();
    await page.mouse.wheel(0, 80);
    await expect.poll(() => counter.innerText()).not.toBe(count);
    expect(await transform(), id).toEqual(before);
    await page.locator("body").dispatchEvent("mousedown");
    await expect(gate).toHaveAttribute("data-viewer-interaction", "inactive");
  }
  // The preference is stable across wheel targets and reloads.
  await page.reload();
  await expect(mode).toHaveAttribute("aria-pressed", "true");
});
