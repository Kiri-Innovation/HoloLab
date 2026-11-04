import { expect, test } from "@playwright/test";

for (const theme of ["light", "dark"]) {
  test(`selected runs delete through one confirmation in ${theme}`, async ({ page }, info) => {
    let runs = Array.from({ length: 3 }, (_, i) => ({
      snapshot_id: `bulkdelete${i}`, workflow_id: "runs-favorites-test",
      created_ts: 1792148400 - i * 3600, node_count: 2, job_count: 3,
      state: "done", state_counts: { done: 3 }, favorite: false, note: null,
    }));
    const deletes: string[] = [];
    await page.route("**/api/workflows/runs-favorites-test/runs**", route => route.fulfill({ json: runs }));
    await page.route("**/api/snapshots/*", route => {
      if (route.request().method() !== "DELETE") return route.fulfill({ json: {} });
      const snapshotId = new URL(route.request().url()).pathname.split("/").at(-1)!;
      deletes.push(snapshotId);
      if (snapshotId === "bulkdelete2") {
        return route.fulfill({ status: 409, json: { detail: { message: "snapshot has live jobs" } } });
      }
      runs = runs.filter(run => run.snapshot_id !== snapshotId);
      return route.fulfill({ json: { snapshot_id: snapshotId, state: "deleted" } });
    });
    await page.goto(`/e2e/fixtures/runs-panel.html?theme=${theme}`);
    const rows = page.locator("[data-hl-run-row]");
    await expect(rows).toHaveCount(3);
    for (const index of [0, 1, 2]) await rows.nth(index).locator('[role="button"]').click({ modifiers: ["Control"] });
    await rows.nth(1).click({ button: "right" });
    const menu = page.locator("[data-hl-run-menu]");
    await expect(menu).toContainText("删除这 3 个 run 及其产物");
    await menu.screenshot({ path: info.outputPath(`menu-${theme}.png`) });
    await menu.getByRole("button").click();
    const modal = page.locator("[data-hl-run-delete-modal]");
    await expect(modal).toContainText("删除这 3 个 run 及其产物");
    await expect(modal).toContainText("不可恢复");
    await modal.screenshot({ path: info.outputPath(`confirm-${theme}.png`) });
    await modal.getByRole("button", { name: "确认删除 3 个 run" }).click();
    await expect(modal.locator("[data-hl-batch-delete-result]")).toContainText("已删除 2 个 run");
    await expect(modal.locator("[data-hl-batch-delete-result]")).toContainText("snapshot has live jobs");
    expect(deletes).toEqual(["bulkdelete0", "bulkdelete1", "bulkdelete2"]);
    await expect(rows).toHaveCount(1);
    await expect(rows.nth(0)).toHaveAttribute("data-hl-run-id", "bulkdelete2");
    await page.screenshot({ path: info.outputPath(`result-${theme}.png`) });
    await modal.getByRole("button", { name: "关闭" }).click();
    // An unselected row retains the original single-run menu wording.
    await page.reload();
    await expect(rows).toHaveCount(1);
    await rows.nth(0).click({ button: "right" });
    await expect(page.locator("[data-hl-run-menu]")).toContainText("删除这个 run 及其产物");
  });
}
