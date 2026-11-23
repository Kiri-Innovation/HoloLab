import { test, expect } from "@playwright/test";

test("gateway directory, delayed input, and unavailable config", async ({ page }) => {
  let mode = "success";
  let release: (() => void) | undefined;
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/workflow-files/config") {
      if (mode === "delayed") await new Promise<void>(resolve => { release = resolve; });
      if (mode === "failure") return route.fulfill({ status: 503, body: "unavailable" });
      return route.fulfill({ json: { workflows_dir: "/isolated/custom" } });
    }
    return route.fulfill({ json: [] });
  });
  await page.goto("/");
  const open = () => page.getByRole("button", { name: "+ \u65b0\u5efa workflow", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "\u65b0\u5efa workflow \u6587\u4ef6" });
  const path = dialog.getByRole("combobox", { name: "workflow-path" });
  await open();
  await expect(path).toHaveValue("/isolated/custom");
  await expect(dialog.getByLabel("Workflow \u6587\u4ef6\u540d")).toHaveValue("untitled");
  await dialog.getByRole("button", { name: "\u53d6\u6d88" }).click();
  mode = "delayed";
  await open();
  await expect.poll(() => !!release).toBe(true);
  await path.fill("/isolated/typed");
  release!();
  await page.waitForTimeout(200);
  await expect(path).toHaveValue("/isolated/typed");
  await dialog.getByRole("button", { name: "\u53d6\u6d88" }).click();
  mode = "failure";
  await open();
  await expect(path).toHaveValue("");
  await dialog.getByRole("button", { name: "\u521b\u5efa" }).click();
  await expect(dialog.getByRole("alert")).toContainText("\u8bf7\u9009\u62e9\u76ee\u5f55");
  await path.fill("/isolated/manual");
  await expect(path).toHaveValue("/isolated/manual");
  await dialog.getByRole("button", { name: "\u53d6\u6d88" }).click();
});

test("create and open submit selected paths to the gateway", async ({ page }) => {
  const requests: { path: string; body: unknown }[] = [];
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/workflow-files/config") return route.fulfill({ json: { workflows_dir: "/isolated/custom" } });
    if (route.request().method() === "POST" && path.startsWith("/api/workflow-files")) {
      requests.push({ path, body: route.request().postDataJSON() });
      return route.fulfill({ status: 422, json: { detail: "isolated test" } });
    }
    return route.fulfill({ json: [] });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "+ \u65b0\u5efa workflow", exact: true }).click();
  const create = page.getByRole("dialog", { name: "\u65b0\u5efa workflow \u6587\u4ef6" });
  await expect(create.getByRole("combobox", { name: "workflow-path" })).toHaveValue("/isolated/custom");
  await create.getByRole("button", { name: "\u521b\u5efa" }).click();
  await expect.poll(() => requests.length).toBe(1);
  expect(requests[0]).toEqual({ path: "/api/workflow-files", body: { parent_dir: "/isolated/custom", name: "untitled" } });
  await create.getByRole("button", { name: "\u53d6\u6d88" }).click();
  await page.getByRole("button", { name: "\u6253\u5f00 workflow", exact: true }).click();
  const open = page.getByRole("dialog", { name: "\u6253\u5f00 workflow \u6587\u4ef6" });
  await open.getByRole("combobox", { name: "workflow-path" }).fill("/isolated/existing.workflow");
  await open.getByRole("button", { name: "\u6253\u5f00", exact: true }).click();
  await expect.poll(() => requests.length).toBe(2);
  expect(requests[1]).toEqual({ path: "/api/workflow-files/open", body: { path: "/isolated/existing.workflow" } });
});
