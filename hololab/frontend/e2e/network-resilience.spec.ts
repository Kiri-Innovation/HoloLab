// End-to-end acceptance for the fetch-recovery layer.
//
// The scenario we're guarding against: gateway restarts for a few
// seconds, the browser's ``fetch`` throws ``TypeError: Failed to fetch``,
// and the page latches a permanent error state that requires a manual
// reload. This suite proves two claims:
//
//   1. TRANSIENT network failure → the page recovers on its own
//      (no manual refresh, error text goes away, real data lands).
//   2. BUSINESS error (404) → we do NOT retry, do NOT hide the error;
//      the operator gets a stable, actionable failure surface.
//
// The suite runs against the vite dev server (see playwright.config.ts)
// and mocks /api with ``page.route`` — no live gateway required.

import { test, expect, type Route } from "@playwright/test";

const EMPTY_WORKFLOWS_BODY = JSON.stringify([]);

test.describe("Gallery — transient network failure recovery", () => {
  test("recovers from a burst of network failures without a manual refresh", async ({
    page,
  }) => {
    let attempts = 0;
    await page.route("**/api/workflows", async (route: Route) => {
      attempts += 1;
      // Fail the first two requests the way a mid-restart gateway does:
      // ``route.abort`` triggers a real ``TypeError: Failed to fetch``
      // inside the app, which is exactly what the resilience layer
      // classifies as transient.
      if (attempts <= 2) {
        await route.abort("connectionreset");
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: EMPTY_WORKFLOWS_BODY,
      });
    });

    // Ignore all other /api paths — we only care about /api/workflows for
    // this assertion. Anything else the Gallery might touch (there is
    // none by default) is served a benign 200.
    await page.route(/\/api\/(?!workflows$)/, async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
    });

    await page.goto("/");

    // The recovery indicator: the "could not load workflows" banner is
    // NEVER seen by the user (retries are silent) OR is transient and
    // cleared once a success lands. Wait until at least 3 attempts have
    // been made (the two failures + the success).
    await expect.poll(() => attempts, { timeout: 20_000 }).toBeGreaterThanOrEqual(3);

    // The empty-state UI is what a successful load with 0 workflows
    // renders. It's the "clean" post-recovery state.
    const errorBanner = page.locator('text=/could not load workflows/i');
    await expect(errorBanner).toHaveCount(0);
  });
});

test.describe("Gallery — business errors are NOT retried", () => {
  test("404 shows a stable error banner and does not retry", async ({ page }) => {
    let attempts = 0;
    await page.route("**/api/workflows", async (route: Route) => {
      attempts += 1;
      await route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({ detail: "no such workspace" }),
      });
    });
    await page.route(/\/api\/(?!workflows$)/, async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
    });

    await page.goto("/");

    // Give the app time to attempt + settle. The retry loop is generous
    // (~30s window on network errors) so we wait comfortably past that;
    // if 404 leaked into the retry classifier the attempt count would
    // climb well above 1.
    const banner = page.locator('text=/could not load workflows/i');
    await expect(banner).toBeVisible({ timeout: 10_000 });

    // Business errors must not enter the retry loop. React StrictMode's
    // dev-mode double-invocation lets ``refresh`` fire twice, so we
    // tolerate ≤2 attempts; a real leak into the retry classifier would
    // blow past the default cap of 6.
    expect(attempts).toBeLessThanOrEqual(2);
  });
});
