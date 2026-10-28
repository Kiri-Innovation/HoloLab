import { describe, expect, it } from "vitest";
import { duplicateNode } from "./nodeDuplicate";

describe("node duplicate", () => {
  it("copies editable algorithm configuration, selects the duplicate, and excludes execution results", () => {
    const source = { id: "original", type: "algorithm", position: { x: 10, y: 20 }, data: {
      pack: { name: "scope/demo", version: "1", manifest_hash: "hash" }, assigned_node_id: "gpu-a",
      params: { steps: 20 }, port_values: { seed: 7 }, arrayed_toggle: true, param_bindings: { steps: "count" },
      runtime: { state: "done", job_id: "job" }, previews: { out: { handle_id: "handle" } }, previewOpen: "out",
    } } as never;
    const copied = duplicateNode(source, "duplicate");
    expect(copied).toMatchObject({ id: "duplicate", selected: true, position: { x: 34, y: 44 }, data: { assigned_node_id: "gpu-a", params: { steps: 20 }, port_values: { seed: 7 }, arrayed_toggle: true } });
    expect(copied.data).not.toHaveProperty("runtime");
    expect(copied.data).not.toHaveProperty("previews");
    expect(copied.data).not.toHaveProperty("previewOpen");
  });
});
