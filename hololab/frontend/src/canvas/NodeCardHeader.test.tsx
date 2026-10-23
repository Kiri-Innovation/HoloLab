import { expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { NodeCardHeader } from "./NodeCardHeader";
import { nodeStatus } from "./nodeStatus";
vi.mock("./CanvasContext", () => ({
  useCanvasContext: () => ({
    workflow_id: "workflow",
    workflow_name: "Example",
  }),
}));
vi.mock("./CopyRefButton", () => ({
  CopyRefButton: ({ kind, id }: { kind: string; id: string }) => (
    <button data-ref={`${kind}/${id}`}>Reference</button>
  ),
}));
it.each([
  ["running", "info"],
  ["done", "success"],
  ["failed", "error"],
  [undefined, "neutral"],
] as const)("shares status and graph reference for %s", (state, tone) => {
  const html = renderToStaticMarkup(
    <NodeCardHeader
      id="assembly"
      name="array-assemble"
      subtitle="system"
      status={nodeStatus({ state })}
      state={state}
      actions={<button>Run</button>}
    />,
  );
  expect(html).toContain(`data-status-tone="${tone}"`);
  expect(html).toContain('data-ref="graph-node/workflow/assembly"');
  expect(html).toContain(">Run</button>");
});
it("does not invent an execution action for constants or viewers", () => {
  const html = renderToStaticMarkup(
    <NodeCardHeader
      id="constant"
      name="float-array"
      subtitle="constant"
      status={{
        tone: "success",
        conclusion: "已就绪",
        reason: "常量值符合格式，无需运行。",
        action: "可直接编辑常量值。",
      }}
    />,
  );
  expect(html).toContain('data-status-tone="success"');
  expect(html).toContain('data-ref="graph-node/workflow/constant"');
  expect(html).not.toContain("hl-node-run-control");
});
