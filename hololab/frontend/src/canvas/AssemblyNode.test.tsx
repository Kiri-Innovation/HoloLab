import { expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { NodeProps } from "@xyflow/react";
vi.mock("@xyflow/react", () => ({
  Handle: () => null, Position: { Left: "left", Right: "right" },
  useReactFlow: () => ({}), useUpdateNodeInternals: () => () => {},
  useNodes: () => [], useEdges: () => [],
}));
vi.mock("./AlgorithmNode", () => ({ AlgorithmNode: () => null, dispatchRunNode: vi.fn() }));
vi.mock("./PinnedParameters", () => ({ PinnedParameters: () => null }));
import { AssemblyNode, ExecutableNode } from "./AssemblyNode";
function card(data: Record<string, unknown>) {
  return renderToStaticMarkup(<AssemblyNode {...{ id: "assembly", data } as NodeProps} />);
}
it.each([undefined, "idle", "done", "failed"])("offers run for %s", state => {
  expect(card({ runtime: { state } })).toContain('aria-label="运行"');
});
it("offers rerun together with the stale notice", () => {
  const html = card({ staleness: { kind: "self_dirty", title: "changed" } });
  expect(html).toContain("需重跑");
  expect(html).toContain('aria-label="重新运行"');
});
it("uses the normal stop control for running tasks", () => {
  expect(card({ runtime: { state: "running", job_id: "job" } })).toContain('aria-label="停止运行"');
});
it("does not allow execution from historical read-only cards", () => {
  expect(card({ readOnly: true })).not.toContain("hl-node-run-control");
});

it.each(["float", "vec3"])("keeps %s constants non-executable", type => {
  const data = { pack: { name: `@builtin/constant-${type}-array`, params: {} }, params: { items: [0], x: [0], y: [0], scale: [1] } };
  const html = renderToStaticMarkup(<ExecutableNode {...{ id: "constant", data } as unknown as NodeProps} />);
  expect(html).toContain("data-hl-constant");
  expect(html).not.toContain("hl-node-run-control");
});
