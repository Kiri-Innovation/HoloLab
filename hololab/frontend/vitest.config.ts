// Vitest config — scoped to the src/ unit tests. The e2e/ folder lives
// under a separate ``playwright test`` runner (see playwright.config.ts);
// vitest picking it up would try to execute the ``@playwright/test``
// harness as a regular unit test and crash.

import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    environment: "node",
  },
});
