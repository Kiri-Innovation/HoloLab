import { expect, test } from "@playwright/test";

for (const action of ["continue", "reload"] as const) {
  test(`stale file patch: ${action} after element LWW`, async ({ page }) => {
    let server = {
      workflow_id: "conflict-test",
      id: "conflict-test",
      name: "Original",
      graph: { nodes: [], edges: [] },
      rev: 0,
      created_ts: 1,
      updated_ts: 1,
    };
    let patches = 0,
      posts = 0,
      reads = 0;
    await page.routeWebSocket("**", (ws) =>
      ws.onMessage((raw) => {
        const { kind, payload: p } = JSON.parse(String(raw));
        if (kind !== "workflow_edit") return;
        patches++;
        if (patches === 1)
          server = { ...server, name: "Other writer", rev: p.base_rev + 1 };
        if (p.base_rev !== server.rev) {
          ws.send(
            JSON.stringify({
              kind: "workflow_changed",
              payload: {
                ...server,
                rejected: true,
                error: "base_rev is behind current revision",
                request_id: p.request_id,
              },
            }),
          );
          return;
        }
        server = {
          ...server,
          name:
            p.ops.find((op: any) => op.op === "name.set")?.value ?? server.name,
          rev: server.rev + 1,
        };
        ws.send(
          JSON.stringify({
            kind: "workflow_changed",
            payload: { ...server, request_id: p.request_id },
          }),
        );
        setTimeout(
          () =>
            ws.send(
              JSON.stringify({
                kind: "workflow_changed",
                payload: { ...server, saved: true },
              }),
            ),
          20,
        );
      }),
    );
    await page.route("**/api/**", async (route) => {
      const req = route.request(),
        path = new URL(req.url()).pathname;
      let body: unknown = [];
      if (path === "/api/workflows" && req.method() === "POST") posts++;
      if (path === "/api/workflows/conflict-test") {
        reads++;
        body = server;
      } else if (path === "/api/nodes/metrics/history")
        body = { sample_interval_s: 3, nodes: {} };
      await route.fulfill({ json: body });
    });
    await page.goto("/w/conflict-test");
    await expect(page.getByPlaceholder("workflow name")).toHaveValue(
      "Original",
    );
    await expect.poll(() => reads).toBeGreaterThanOrEqual(2);
    server = { ...server, name: "Other writer", rev: 1 };
    await page.getByPlaceholder("workflow name").fill("Local wins");
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect.poll(() => server.name).toBe("Local wins");
    expect(patches).toBe(2);
    expect(posts).toBe(0);
    if (action === "continue") {
      await dialog
        .getByRole("button", { name: "继续编辑", exact: true })
        .click();
      await page.getByPlaceholder("workflow name").fill("Continue editing");
      await expect.poll(() => server.name).toBe("Continue editing");
    } else {
      await dialog
        .getByRole("button", { name: "重载放弃本地", exact: true })
        .click();
      await expect(page.getByPlaceholder("workflow name")).toHaveValue(
        "Local wins",
      );
      await expect(dialog).toHaveCount(0);
    }
  });
}
