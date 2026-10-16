import { expect, test } from "@playwright/test";

for (const theme of ["light", "dark"]) {
  test(`favorites preserve chronology and use only stars in ${theme}`, async ({ page }, info) => {
    const runs = [false, true, false, true].map((favorite, i) => ({
      snapshot_id: `abcdef0${i}`, workflow_id: "runs-favorites-test",
      created_ts: 1792148400 - i * 3600, node_count: 16, job_count: 115,
      state: "done", state_counts: { done: 115 }, favorite, note: null,
    }));
    const patches: unknown[] = [];
    let rejectPatch = false;
    await page.route("**/api/workflows/runs-favorites-test/runs**", async route => {
      if (route.request().method() === "PATCH") {
        const patch = route.request().postDataJSON();
        patches.push(patch);
        if (rejectPatch) return route.fulfill({ status: 400, json: { detail: "test rejection" } });
        const run = runs.find(r => route.request().url().endsWith(r.snapshot_id))!;
        Object.assign(run, patch);
        return route.fulfill({ json: run });
      }
      return route.fulfill({ json: runs });
    });
    await page.goto(`/e2e/fixtures/runs-panel.html?theme=${theme}`);
    const panel = page.locator(".hl-runs-panel");
    const rows = panel.locator("[data-hl-run-row]");
    const star = (i: number) => rows.nth(i).getByRole("button", { name: /^(取消收藏|收藏此 run)$/ });
    const checkOrder = async () => {
      await expect(rows).toHaveCount(4);
      for (let i = 0; i < 4; i++) await expect(rows.nth(i)).toContainText(`abcdef0${i}`);
    };
    await checkOrder();
    await expect(panel.getByText("收藏", { exact: true })).toHaveCount(0);
    await expect(panel.getByRole("button", { name: "检查", exact: true })).toHaveCount(0);
    await expect(star(1)).toHaveCSS("color", "rgb(245, 197, 24)");
    await expect(star(1).locator("svg")).toHaveAttribute("fill", "currentColor");
    await expect(star(0).locator("svg")).toHaveAttribute("fill", "none");
    for (let i = 0; i < 4; i++) await expect(rows.nth(i)).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
    await panel.screenshot({ path: info.outputPath(`runs-${theme}.png`) });
    await star(0).hover();
    await expect(star(0)).toHaveCSS("opacity", "1");
    await expect(star(0)).not.toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
    await star(0).click();
    await expect(star(0)).toHaveAttribute("aria-pressed", "true");
    await expect.poll(() => patches.length).toBe(1);
    expect(patches[0]).toEqual({ favorite: true });
    await checkOrder();
    await expect(panel.locator("[data-hl-current-run]")).toHaveCount(0);
    await panel.getByRole("button", { name: "刷新", exact: true }).click();
    await expect(star(0)).toHaveAttribute("aria-pressed", "true");
    await page.reload();
    await checkOrder();
    await expect(star(0)).toHaveAttribute("aria-pressed", "true");
    await star(0).click();
    await expect.poll(() => patches.length).toBe(2);
    expect(patches[1]).toEqual({ favorite: false });
    await page.reload();
    await checkOrder();
    await expect(star(0)).toHaveAttribute("aria-pressed", "false");
    rejectPatch = true;
    await star(0).click();
    await expect.poll(() => patches.length).toBe(3);
    await expect(star(0)).toHaveAttribute("aria-pressed", "false");
    await checkOrder();
    await rows.nth(2).getByText("abcdef02").click();
    await expect(rows.nth(2).locator("[data-hl-current-run]")).toHaveCount(1);
    await page.goto(`/e2e/fixtures/runs-panel.html?theme=${theme}&diff=1`);
    await panel.getByRole("button", { name: "检查", exact: true }).click();
    await expect(page.getByText("新增节点 Camera", { exact: true })).toBeVisible();
  });
}
