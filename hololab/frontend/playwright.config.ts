// Playwright config for the frontend e2e suite.
//
// Focused on the network-resilience acceptance tests: the suite spins up
// the vite dev server (same one developers use) and intercepts /api
// traffic with ``page.route`` so we can simulate the "gateway restart"
// window without actually killing a real server. That's the scenario the
// resilience layer exists to survive.
//
// Browser: chromium only — the code paths we care about (WS reconnect,
// TypeError from fetch) are identical across engines and the CI cost of
// running three copies isn't worth it for a targeted suite.

import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:5173",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "npm run dev -- --host 127.0.0.1 --port 5173",
    port: 5173,
    // Always reuse a dev server already listening on 5173 — the human
    // developer usually has one open, and letting Playwright kill it
    // between runs makes ``npx playwright test`` a hostile action.
    // CI environments start fresh anyway, so the practical effect is
    // "attach to whatever's there, otherwise spawn one".
    reuseExistingServer: true,
    timeout: 60_000,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
