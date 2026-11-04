import { expect, test } from "@playwright/test";

for (const theme of ["light", "dark"]) {
  test(`run rows support modifier and range selection without opening them in ${theme}`, async ({ page }, info) => {
    const runs = Array.from({ length: 5 }, (_, i) => ({
      snapshot_id: `multirun${i}`, workflow_id: "runs-favorites-test",
      created_ts: 1792148400 - i * 3600, node_count: 2, job_count: 3,
      state: "done", state_counts: { done: 3 }, favorite: false, note: null,
    }));
    const patches: unknown[] = [];
    await page.route("**/api/workflows/runs-favorites-test/runs**", async route => {
      if (route.request().method() === "PATCH") {
        patches.push(route.request().postDataJSON());
        return route.fulfill({ json: {} });
      }
      return route.fulfill({ json: runs });
    });
    await page.goto(`/e2e/fixtures/runs-panel.html?theme=${theme}`);
    const rows = page.locator("[data-hl-run-row]");
    await expect(rows).toHaveCount(5);

    // Ctrl and Cmd both toggle, with no snapshot open as a side effect.
    await rows.nth(1).locator('[role="button"]').click({ modifiers: ["Control"] });
    await expect(rows.nth(1)).toHaveAttribute("data-hl-run-selected", "true");
    await expect(rows.nth(1).locator("[data-hl-current-run]")).toHaveCount(0);
    await rows.nth(3).locator('[role="button"]').click({ modifiers: ["Meta"] });
    await expect(rows.nth(1)).toHaveAttribute("data-hl-run-selected", "true");
    await expect(rows.nth(3)).toHaveAttribute("data-hl-run-selected", "true");

    // The last modifier-clicked row is the range anchor. Shift selects exactly
    // that contiguous interval and still does not open a run.
    await rows.nth(4).locator('[role="button"]').click({ modifiers: ["Shift"] });
    for (const index of [3, 4]) await expect(rows.nth(index)).toHaveAttribute("data-hl-run-selected", "true");
    for (const index of [0, 1, 2]) await expect(rows.nth(index)).not.toHaveAttribute("data-hl-run-selected");
    await expect(page.locator("[data-hl-current-run]")).toHaveCount(0);
    await page.locator(".hl-runs-panel").screenshot({ path: info.outputPath(`range-selected-${theme}.png`) });

    // An unmodified click keeps the established open behaviour and reduces the
    // selection to that row.
    await rows.nth(0).locator('[role="button"]').click();
    await expect(rows.nth(0)).toHaveAttribute("data-hl-run-selected", "true");
    for (const index of [1, 2, 3, 4]) await expect(rows.nth(index)).not.toHaveAttribute("data-hl-run-selected");
    await expect(rows.nth(0).locator("[data-hl-current-run]")).toHaveCount(1);

    // Inline controls remain controls: the star must not alter selection.
    await rows.nth(0).locator('button[aria-label="收藏此 run"]').click();
    expect(patches).toEqual([{ favorite: true }]);
    await expect(rows.nth(0)).toHaveAttribute("data-hl-run-selected", "true");
    await expect(rows.nth(1)).not.toHaveAttribute("data-hl-run-selected");
    await page.locator(".hl-runs-panel").screenshot({ path: info.outputPath(`multiselect-${theme}.png`) });
  });
}
