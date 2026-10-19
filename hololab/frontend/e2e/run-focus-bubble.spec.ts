import { expect, test } from "@playwright/test";

test("repeated jumps replace the bubble, including a newly fetched run", async ({ page }) => {
  let includeNew = false;
  await page.route("**/api/workflows/runs-favorites-test/runs", route => route.fulfill({ json:
    ["abcdef00", ...Array.from({ length: 30 }, (_, i) => `filler-${i}`), "abcdef03", ...(includeNew ? ["new-run"] : [])].map(snapshot_id => ({
      snapshot_id, created_ts: 1, state: "done", state_counts: { done: 1 }, node_count: 1, job_count: 1,
    })),
  }));
  await page.goto("/e2e/fixtures/runs-panel.html?focus=1");
  const bubble = page.locator("[data-hl-run-focus]");
  for (const id of ["abcdef00", "abcdef03", "abcdef03", "new-run"]) {
    includeNew = id === "new-run";
    await page.getByRole("button", { name: `Jump ${id}`, exact: true }).click();
    await expect(bubble).toHaveCount(1);
    await expect(bubble).toHaveAttribute("data-hl-run-focus", id);
    await expect(bubble).toBeVisible();
    await expect(page.locator(`[data-hl-run-id="${id}"]`)).toBeInViewport();
  }
  await bubble.getByRole("button", { name: "关闭 run 定位提示" }).click();
  await expect(bubble).toHaveCount(0);
});
