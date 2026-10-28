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

it.each([
  ["builtin", "内置算法：随 HoloLab 提供，结果可复现"],
  ["community", "社区发行版本：与注册表发布内容一致，结果可复现"],
] as const)("shows an accessible %s shield", (certification, message) => {
  const html = renderToStaticMarkup(
    <NodeCardHeader
      id={`${certification}-pack`}
      name={`${certification}/colmap-sfm`}
      version="0.4.2"
      subtitle="local"
      certification={certification}
      status={nodeStatus({})}
    />,
  );
  expect(html).toContain('data-hl-certified=""');
  expect(html).toContain(`title="${message}"`);
  expect(html).toContain(`aria-label="${message}"`);
  expect(html).toContain('width="11"');
  expect(html).toContain('stroke-width="1.25"');
});

it("omits the shield when the release is not community-certified", () => {
  const html = renderToStaticMarkup(
    <NodeCardHeader
      id="builtin-pack"
      name="kiri/colmap-sfm"
      version="0.4.2"
      subtitle="local"
      status={nodeStatus({})}
    />,
  );
  expect(html).not.toContain("data-hl-certified");
});

it("uses the canvas-resolved label everywhere the header exposes a name", () => {
  const html = renderToStaticMarkup(
    <NodeCardHeader
      id="source"
      name="builtin/video-array-source"
      displayName="video-array-source"
      version="0.1.0"
      subtitle="local"
      status={nodeStatus({})}
    />,
  );
  expect(html).toContain(">video-array-source</span>");
  expect(html).toContain('data-tooltip-title="video-array-source"');
  expect(html).not.toContain("builtin/video-array-source");
});
