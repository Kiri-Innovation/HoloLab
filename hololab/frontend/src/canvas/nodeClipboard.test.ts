import { describe, expect, it } from "vitest";
import { nodeClipboardCopy, pastedNodePosition } from "./nodeClipboard";

describe("node clipboard", () => {
  it("copies editable algorithm configuration but never execution results", () => {
    const source = {
      id: "original", type: "algorithm", position: { x: 10, y: 20 }, data: {
        pack: { name: "scope/demo", version: "1", manifest_hash: "hash" },
        assigned_node_id: "gpu-a", params: { steps: 20 }, port_values: { seed: 7 },
        arrayed_toggle: true, param_bindings: { steps: "count" },
        runtime: { state: "done", job_id: "job" }, previews: { out: { handle_id: "handle" } }, previewOpen: "out",
      },
    } as never;
    const copied = nodeClipboardCopy(source);
    expect(copied.data).toMatchObject({ assigned_node_id: "gpu-a", params: { steps: 20 }, port_values: { seed: 7 }, arrayed_toggle: true });
    expect(copied.data).not.toHaveProperty("runtime");
    expect(copied.data).not.toHaveProperty("previews");
    expect(copied.data).not.toHaveProperty("previewOpen");
  });

  it("offsets pasted nodes from the right-click point", () => {
    expect(pastedNodePosition({ x: 100, y: 200 })).toEqual({ x: 124, y: 224 });
  });
});
