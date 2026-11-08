import { describe, it, expect } from "vitest";
import { applyOps, diffDocument, type DocumentState } from "./workflowPatch";
const make = (): DocumentState => ({
  name: "one",
  graph: {
    nodes: [
      {
        id: "a",
        algorithm_name: "p",
        algorithm_version: "1",
        params: { x: 1, y: 1 },
        position: { x: 0, y: 0 },
        assigned_node_id: null,
      },
    ],
    edges: [],
  },
});
describe("workflow element patches", () => {
  it("preserves remote fields when rebasing same-node parameter edits", () => {
    const before = make(),
      local = make(),
      remote = make();
    local.graph.nodes[0].params = { x: 2, y: 1 };
    remote.graph.nodes[0].params = { x: 1, y: 3 };
    const ops = diffDocument(before, local);
    expect(ops).toEqual([
      {
        op: "node.map.set",
        id: "a",
        field: "params",
        key: "x",
        value: 2,
        remove: false,
      },
    ]);
    expect(applyOps(remote, ops).graph.nodes[0].params).toEqual({ x: 2, y: 3 });
  });
  it("roundtrips every persisted canvas field and graph mutation", () => {
    const before = make(),
      after = make();
    after.name = "renamed";
    Object.assign(after.graph.nodes[0], {
      position: { x: 5, y: 7 },
      params: { z: 9 },
      param_bindings: { z: "v" },
      port_values: { in: 1 },
      local_params: ["z"],
      assigned_node_id: "device",
      arrayed_toggle: true,
      parallelism: 3,
      title: "title",
      preview_open: "out",
      code_identity: { manifest_hash: "h" },
    });
    after.graph.variables = { v: { type: "int", default: 1 } };
    after.graph.variable_values = { v: 4 };
    after.graph.pinned_params = { a: ["z"] };
    after.graph.nodes.push({
      id: "b",
      kind: "view",
      view_type: "artifact-preview",
      title: "visualize",
    } as any);
    after.graph.edges.push({
      id: "e",
      source: "a",
      sourceHandle: "out",
      target: "b",
      targetHandle: "in",
    });
    expect(applyOps(before, diffDocument(before, after))).toEqual(after);
  });
  it("deleting nodes removes incident edges and does not resurrect remote-deleted nodes", () => {
    const before = make();
    before.graph.edges = [
      {
        id: "e",
        source: "a",
        sourceHandle: "o",
        target: "a",
        targetHandle: "i",
      },
    ];
    expect(applyOps(before, [{ op: "node.remove", id: "a" }]).graph).toEqual({
      nodes: [],
      edges: [],
    });
    expect(
      applyOps(
        { name: "x", graph: { nodes: [], edges: [] } },
        diffDocument(make(), {
          ...make(),
          name: "x",
          graph: {
            nodes: [{ ...make().graph.nodes[0], params: { x: 2 } }],
            edges: [],
          },
        }),
      ).graph.nodes,
    ).toEqual([]);
  });
});
