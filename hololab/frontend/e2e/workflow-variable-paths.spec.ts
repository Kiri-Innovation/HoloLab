import { expect, test, type Page } from "@playwright/test";
import { applyOps } from "../src/canvas/workflowPatch";
import type { GraphNode, WorkflowGraph } from "../src/wire";

const workflowId = "variable-path-preview";
const pack = {
  name: "builtin/single-video-source", version: "0.1.0", manifest_hash: "test",
  inputs: {}, outputs: {}, params: { video_path: { type: "path", path_kind: "file" } },
};
const source: GraphNode = {
  id: "source", algorithm_name: pack.name, algorithm_version: pack.version,
  position: { x: 100, y: 100 }, params: {}, assigned_node_id: null,
  param_bindings: { video_path: "input_video" },
};

async function openWorkflow(page: Page, {
  online = ["device-a"], offerings = online, bindings = {}, nodes = [source], pathDefault = "",
}: {
  online?: string[]; offerings?: string[]; bindings?: Record<string, string>; nodes?: GraphNode[]; pathDefault?: string;
} = {}) {
  const graph: WorkflowGraph = {
    variables: { input_video: { type: "path", default: pathDefault }, frames: { type: "int", default: 5 }, offset: { type: "float", default: 0.5 } },
    variable_values: {}, nodes, edges: [],
  };
  const saves: WorkflowGraph[] = [];
  const browsed: string[] = [];
  let deviceState = { workflow_id: workflowId, revision: 0, bindings: {...bindings} };
  let document = {
    id: workflowId, workflow_id: workflowId, name: "Workflow variable path preview",
    created_ts: 1, updated_ts: 1, rev: 0, path: "/preview/workflow.workflow", graph,
  };
  await page.routeWebSocket("**/*", socket => {
    socket.onMessage(raw => {
      const { kind, payload } = JSON.parse(String(raw));
      if (kind !== "workflow_edit") return;
      document = { ...document, ...applyOps(document, payload.ops), rev: document.rev + 1 };
      saves.push(document.graph);
      socket.send(JSON.stringify({
        kind: "workflow_changed", payload: { ...document, request_id: payload.request_id },
      }));
      socket.send(JSON.stringify({ kind: "workflow_changed", payload: { ...document, saved: true } }));
    });
  });
  await page.route("**/api/**", async route => {
    const url = new URL(route.request().url());
    const workflowPath = `/api/workflows/${workflowId}`;
    let body: unknown = [];
    if (url.pathname === workflowPath) body = document;
    if (url.pathname === `${workflowPath}/device-bindings`) {
      if (route.request().method() === "PATCH") {
        const patch = route.request().postDataJSON();
        expect(patch.base_revision).toBe(deviceState.revision);
        const next = {...deviceState.bindings};
        for (const [key, node] of Object.entries(patch.bindings as Record<string, string | null>)) {
          if (node === null) delete next[key]; else next[key] = node;
        }
        deviceState = {...deviceState, revision: deviceState.revision + 1, bindings: next};
      }
      body = deviceState;
    }
    if (url.pathname === `${workflowPath}/analyze`) body = {
      graph: { ...graph, nodes: nodes.map(node => ({ ...node, assigned_node_id: online[0] })) },
      staleness: {}, diff: [], environment_params: {},
    };
    if (url.pathname === "/api/pack-catalog") body = [{ ...pack, node_ids: offerings }];
    if (url.pathname === "/api/nodes") body = online.map(id => ({
      node_id: id, node_name: id, gpu: { count: 0 }, packs: [], pack_dirs: [], workspace_root: `/media/${id}`,
    }));
    if (url.pathname === "/api/nodes/metrics/history") body = { sample_interval_s: 5, nodes: {} };
    const pathRequest = /^\/api\/nodes\/([^/]+)\/paths$/.exec(url.pathname);
    if (pathRequest) {
      browsed.push(pathRequest[1]);
      const path = url.searchParams.get("path") ?? "/";
      body = {
        path, parent: "/", next_offset: null,
        entries: ["shared.mp4", "local.mp4"].filter(name => name.startsWith(url.searchParams.get("prefix") ?? ""))
          .map(name => ({ name, path: path + name, kind: "file", symlink: false, size: 4096, modified_ts: null })),
      };
    }
    await route.fulfill({ json: body });
  });
  await page.goto(`/w/${workflowId}`);
  await page.getByRole("tab", { name: (page.viewportSize()?.width ?? 1440) <= 900 ? "Inspect" : "Workflow", exact: true }).click();
  await page.getByText("工作流变量（3）", { exact: true }).click();
  return { saves, browsed, getDeviceState: () => deviceState };
}

const editorButton = (page: Page, name: string) => page.getByRole("button", { name: `编辑变量 ${name}`, exact: true });
const pathDialog = (page: Page) => page.getByRole("dialog", { name: /^Browse compute node/ });

const browseButton = (page: Page, label: string) => page.getByRole("button", { name: `Browse paths for input_video ${label}` });

test("edits effective values inline, inherits defaults, and restores an override", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1080 });
  const inherited = "/media/device-a/inherited.mp4";
  const { saves, browsed } = await openWorkflow(page, {pathDefault: inherited});
  const rows = page.locator(".hl-variable-list-row");
  await expect(rows).toHaveCount(3);
  const row = rows.first();
  await expect(row.locator("strong")).toHaveText("input_video");
  expect((await row.boundingBox())!.height).toBeLessThan(45);
  const local = row.getByRole("combobox", { name: "input_video 当前值" });
  await expect(local).toHaveValue(inherited);
  await expect(row.locator(".hl-variable-inline-value")).toHaveAttribute("data-value-source", "default");
  await expect(page.getByRole("combobox", { name: "input_video 默认值" })).toHaveCount(0);
  await local.focus();
  await expect.poll(() => browsed.length).toBeGreaterThan(0);
  await local.blur();
  expect(saves).toHaveLength(0);

  await editorButton(page, "input_video").click();
  const editor = page.getByRole("dialog", { name: "input_video", exact: true });
  await expect(editor).toBeVisible();
  const rowBounds = (await row.boundingBox())!;
  expect((await editor.boundingBox())!.x).toBeGreaterThanOrEqual(rowBounds.x + rowBounds.width);
  const shared = editor.getByRole("combobox", { name: "input_video 默认值" });
  const restore = editor.getByRole("button", { name: "使用默认值" });
  const closeEditor = editor.getByRole("button", { name: "关闭变量编辑" });
  await expect(shared).toHaveValue(inherited);
  await expect(restore).toBeDisabled();

  const dialog = pathDialog(page);
  async function pick(label: string, file: string) {
    await browseButton(page, label).click();
    await expect(dialog.getByLabel("Location", { exact: true })).toHaveValue("/media/device-a/");
    await dialog.getByRole("option", { name: new RegExp(file) }).click();
    await dialog.getByRole("button", { name: "Select", exact: true }).click();
    await expect(dialog).toBeHidden();
  }
  await pick("默认值", "shared.mp4");
  await expect(editor).toBeVisible();
  await expect(local).toHaveValue("/media/device-a/shared.mp4");
  await expect.poll(() => saves.at(-1)?.variables?.input_video.default).toBe("/media/device-a/shared.mp4");
  expect(saves.at(-1)?.variable_values).not.toHaveProperty("input_video");
  await closeEditor.click();

  await local.fill("/manual/clip.mp4");
  await expect.poll(() => saves.at(-1)?.variable_values?.input_video).toBe("/manual/clip.mp4");
  await local.fill(inherited);
  await pick("当前值", "local.mp4");
  await expect(local).toHaveValue("/media/device-a/local.mp4");
  await expect(row.locator(".hl-variable-inline-value")).toHaveAttribute("data-value-source", "local");
  await expect.poll(() => saves.at(-1)?.variable_values?.input_video).toBe("/media/device-a/local.mp4");
  expect(saves.at(-1)?.variables?.input_video.default).toBe("/media/device-a/shared.mp4");
  expect([...new Set(browsed)]).toEqual(["device-a"]);
  await page.screenshot({ path: testInfo.outputPath("inline-values.png") });

  await local.fill("");
  await expect.poll(() => saves.at(-1)?.variable_values?.input_video).toBe("");
  await expect(local).toHaveValue("");
  await editorButton(page, "input_video").click();
  await expect(restore).toBeEnabled();
  await restore.click();
  await expect(local).toHaveValue("/media/device-a/shared.mp4");
  await expect(restore).toBeDisabled();
  await expect.poll(() => saves.at(-1)?.variable_values).not.toHaveProperty("input_video");
  await closeEditor.click();

  const frames = page.getByRole("spinbutton", { name: "frames 当前值" });
  const offset = page.getByRole("spinbutton", { name: "offset 当前值" });
  await expect(frames).toHaveAttribute("step", "1");
  await expect(offset).toHaveAttribute("step", "any");
  await expect(frames).toHaveValue("5");
  await expect(offset).toHaveValue("0.5");
  await frames.fill("12");
  await offset.fill("0.75");
  await expect.poll(() => saves.at(-1)?.variable_values?.frames).toBe(12);
  await expect.poll(() => saves.at(-1)?.variable_values?.offset).toBe(0.75);
  await offset.fill("");
  await expect.poll(() => saves.at(-1)?.variable_values?.offset).toBeNull();
  await expect(offset).toHaveValue("");
  expect(saves.at(-1)?.variables?.frames.default).toBe(5);
  expect(saves.at(-1)?.variables?.offset.default).toBe(0.5);
});

for (const { title, options, target } of [
  { title: "logical binding takes precedence over a legacy assignment", options: {
    online: ["device-a", "device-b"], bindings: { capture: "device-b" },
    nodes: [{ ...source, device: "capture", assigned_node_id: "device-a" }],
  }, target: "device-b" },
  { title: "legacy assignment disambiguates two eligible devices", options: {
    online: ["device-a", "device-b"], nodes: [{ ...source, assigned_node_id: "device-b" }],
  }, target: "device-b" },
  { title: "the package is available on only one of two online devices", options: {
    online: ["device-a", "device-b"], offerings: ["device-b"],
  }, target: "device-b" },
  { title: "an unbound variable uses the only online node", options: { nodes: [] }, target: "device-a" },
  { title: "multiple automatic candidates", options: { online: ["device-a", "device-b"] }, target: null },
  { title: "consumers on different devices", options: {
    online: ["device-a", "device-b"],
    nodes: [{ ...source, assigned_node_id: "device-a" }, { ...source, id: "other", assigned_node_id: "device-b" }],
  }, target: null },
  { title: "explicit binding to an offline device", options: { bindings: { default: "offline" } }, target: null },
]) {
  test(`chooses a browse filesystem for ${title}`, async ({ page }) => {
    const { browsed, saves } = await openWorkflow(page, options);
    await editorButton(page, "input_video").click();
    const shared = browseButton(page, "默认值");
    const local = browseButton(page, "当前值");
    if (target) {
      await expect(shared).toBeEnabled();
      await expect(local).toBeEnabled();
      await local.click();
      await expect(pathDialog(page).getByLabel("Location", { exact: true })).toHaveValue(`/media/${target}/`);
      await expect.poll(() => browsed).toEqual([target]);
    } else {
      await expect(shared).toBeDisabled();
      await expect(local).toBeDisabled();
      await page.getByRole("combobox", { name: "input_video 当前值" }).fill("/typed.mp4");
      await expect.poll(() => saves.at(-1)?.variable_values?.input_video).toBe("/typed.mp4");
      expect(browsed).toEqual([]);
    }
  });
}

test("keeps the narrow editor within the viewport and Escape closes only the path picker first", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 700 });
  const path = "/media/device-a/" + "nested-directory/".repeat(14) + "file.mp4";
  await openWorkflow(page, {pathDefault: path});
  const inline = page.getByRole("combobox", {name: "input_video 当前值"});
  await expect(inline).toHaveValue(path);
  await expect(inline).toHaveAttribute("title", path);
  const row = page.locator(".hl-variable-list-row").first();
  expect(await row.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
  expect((await row.boundingBox())!.height).toBeLessThan(45);
  const trigger = editorButton(page, "input_video");
  await trigger.click();
  const editor = page.getByRole("dialog", { name: "input_video", exact: true });
  await expect(editor).toBeVisible();
  const { x, y, width, height } = (await editor.boundingBox())!;
  expect(x).toBeGreaterThanOrEqual(0);
  expect(y).toBeGreaterThanOrEqual(0);
  expect(x + width).toBeLessThanOrEqual(390);
  expect(y + height).toBeLessThanOrEqual(700);
  await page.screenshot({ path: testInfo.outputPath("variables-narrow.png") });
  await browseButton(page, "默认值").click();
  await expect(pathDialog(page)).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(pathDialog(page)).toBeHidden();
  await expect(editor).toBeVisible();
  await editor.getByRole("button", { name: "关闭变量编辑" }).click();
  await expect(trigger).toBeFocused();
});

test("edits declaration details and moves the local value and node binding when renamed", async ({ page }) => {
  const { saves } = await openWorkflow(page);
  await page.getByRole("combobox", { name: "input_video 当前值" }).fill("/manual.mp4");
  await editorButton(page, "input_video").click();
  await page.getByRole("textbox", { name: "input_video 说明" }).fill("Preview input");
  await page.getByRole("button", { name: "改名" }).click();
  await page.getByRole("textbox", { name: "input_video 新名称" }).fill("renamed");
  await page.getByRole("button", { name: "保存名称" }).click();
  await expect(editorButton(page, "renamed")).toBeVisible();
  await expect.poll(() => saves.at(-1)?.variables?.renamed?.description).toBe("Preview input");
  await expect.poll(() => saves.at(-1)?.variable_values).toEqual({renamed: "/manual.mp4"});
  await expect.poll(() => saves.at(-1)?.nodes[0].param_bindings?.video_path).toBe("renamed");
  await editorButton(page, "renamed").click();
  page.once("dialog", dialog => dialog.accept());
  await page.getByRole("button", {name: "删除声明"}).click();
  await expect(editorButton(page, "renamed")).toHaveCount(0);
  await expect.poll(() => saves.at(-1)?.variable_values).toEqual({});
  await expect.poll(() => saves.at(-1)?.variables).not.toHaveProperty("renamed");
});

test("adds devices and variables from compact header buttons without toggling the variable list", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const { saves, getDeviceState } = await openWorkflow(page);
  const deviceSection = page.getByRole("region", { name: "设备", exact: true });
  const variableSection = page.locator(".hl-workflow-variables-panel");
  const summary = variableSection.locator("summary");
  const addDevice = deviceSection.getByRole("button", { name: "添加设备", exact: true });
  const addVariable = variableSection.getByRole("button", { name: "添加变量", exact: true });
  for (const [header, trigger, label] of [
    [deviceSection.locator("header"), addDevice, "添加设备"],
    [summary, addVariable, "添加变量"],
  ] as const) {
    await expect(trigger).toHaveText("+");
    await expect(trigger).toHaveAttribute("title", label);
    await expect(trigger).toHaveAttribute("aria-expanded", "false");
    const headerBounds = (await header.boundingBox())!;
    const buttonBounds = (await trigger.boundingBox())!;
    expect(Math.abs(headerBounds.x + headerBounds.width - buttonBounds.x - buttonBounds.width)).toBeLessThan(2);
    expect(buttonBounds.y).toBeGreaterThanOrEqual(headerBounds.y - 1);
    expect(buttonBounds.width).toBeLessThan(36);
  }
  await expect(page.getByRole("textbox", { name: "新逻辑设备名称" })).toHaveCount(0);
  await expect(page.getByRole("textbox", { name: "新变量名" })).toHaveCount(0);

  await addDevice.click();
  const deviceForm = page.getByRole("dialog", { name: "添加设备", exact: true });
  await expect(deviceForm).toBeVisible();
  expect((await deviceForm.boundingBox())!.x).toBeGreaterThan((await addDevice.boundingBox())!.x);
  const deviceName = deviceForm.getByRole("textbox", { name: "新逻辑设备名称" });
  await expect(deviceName).toBeFocused();
  await deviceName.fill("    ");
  await expect(deviceForm.getByRole("button", { name: "添加设备", exact: true })).toBeDisabled();
  await deviceName.fill("cancelled-device");
  await deviceForm.getByRole("button", { name: "取消", exact: true }).click();
  await expect(addDevice).toBeFocused();
  await expect(page.getByRole("combobox", { name: "设备 cancelled-device" })).toHaveCount(0);
  await addDevice.click();
  await expect(deviceName).toHaveValue("");
  await deviceName.fill("  capture  ");
  await deviceForm.getByRole("button", { name: "添加设备", exact: true }).click();
  await expect(deviceForm).toBeHidden();
  const capture = page.getByRole("combobox", { name: "设备 capture", exact: true });
  await expect(capture).toHaveValue("");
  expect(getDeviceState().bindings).toEqual({});
  await capture.selectOption("device-a");
  await expect.poll(() => getDeviceState().bindings).toEqual({capture: "device-a"});
  await expect(capture).toHaveValue("device-a");

  await summary.click();
  const details = variableSection.locator("details");
  await expect(details).not.toHaveAttribute("open", "");
  await addVariable.click();
  const variableForm = page.getByRole("dialog", { name: "添加变量", exact: true });
  const variableName = variableForm.getByRole("textbox", { name: "新变量名" });
  await expect(variableForm).toBeVisible();
  await expect(details).not.toHaveAttribute("open", "");
  await expect(variableName).toBeFocused();
  await variableName.fill("cancelled-variable");
  await variableForm.getByRole("button", { name: "取消", exact: true }).click();
  await expect(addVariable).toBeFocused();
  expect(saves).toHaveLength(0);

  await addVariable.press("Space");
  const submit = variableForm.getByRole("button", { name: "添加变量", exact: true });
  await submit.click();
  await expect(variableForm.getByRole("alert")).toHaveText("变量名不能为空或重复");
  await variableName.fill("input_video");
  await submit.click();
  await expect(variableForm.getByRole("alert")).toBeVisible();
  expect(saves).toHaveLength(0);
  await variableName.fill("  ratio  ");
  await variableForm.getByRole("combobox", { name: "新变量类型" }).selectOption("float");
  await submit.click();
  await expect(variableForm).toBeHidden();
  await expect(details).not.toHaveAttribute("open", "");
  await expect.poll(() => saves.at(-1)?.variables?.ratio).toEqual({type:"float", description:"", default:null});
  await summary.click();
  await editorButton(page, "ratio").click();
  const editor = page.getByRole("dialog", { name: "ratio", exact: true });
  await expect(editor.getByRole("spinbutton", { name: "ratio 默认值" })).toHaveAttribute("step", "any");
  await editor.getByRole("button", { name: "关闭变量编辑" }).click();
  await editorButton(page, "input_video").click();
  await expect(browseButton(page, "默认值")).toBeEnabled();
  await expect(browseButton(page, "当前值")).toBeEnabled();
  expect(getDeviceState().bindings).toEqual({capture:"device-a"});
});
