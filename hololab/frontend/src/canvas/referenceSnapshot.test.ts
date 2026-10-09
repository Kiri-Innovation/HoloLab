import { describe, expect, it } from "vitest";
import { referenceSnapshotDocument, type ResolvedReference } from "./referenceSnapshot";

function section(document: string, name: string) {
  return JSON.parse(document.split(`## ${name}\n\`\`\`json\n`)[1].split("\n```")[0]);
}

const workflow: ResolvedReference = {
  kind: "workflow",
  ref: "hololab://workflow/11111111-1111-1111-1111-111111111111",
  resource: {
    workflow_id: "11111111-1111-1111-1111-111111111111",
    name: "Demo",
    graph: {
      nodes: [{ id: "n1", algorithm_name: "demo-echo", algorithm_version: "0.1.0", latest_run: { state: "done" } }],
      edges: [],
    },
    last_run: { state: "done", created_ts: 123, snapshot_id: "run-1" },
  },
  related: { workflow: "/api/workflows/11111111-1111-1111-1111-111111111111" },
};

describe("workflow reference snapshot", () => {
  it("includes identity, run state, compact nodes and instance navigation", () => {
    const doc = referenceSnapshotDocument(workflow, "http://localhost:8123");
    expect(section(doc, "identity")).toMatchObject({ workflow_id: workflow.resource.workflow_id, name: "Demo" });
    expect(section(doc, "state")).toMatchObject({ node_count: 1, edge_count: 0, last_run: workflow.resource.last_run });
    expect(section(doc, "context").nodes).toEqual([{ id: "n1", algorithm: "demo-echo@0.1.0", state: "done" }]);
    const navigation = section(doc, "navigation");
    expect(navigation.instance_base_url).toBe("http://localhost:8123");
    expect(new URL(navigation.resolve).searchParams.get("ref")).toBe(workflow.ref);
    expect(navigation.workflow).toBe(workflow.related!.workflow);
  });

  it("bounds large graphs while counting states across every node", () => {
    const nodes = Array.from({ length: 120 }, (_, i) => ({ id: `n${i}`, algorithm_name: "echo", algorithm_version: "1", params: { huge: "omit me" } }));
    const doc = referenceSnapshotDocument({ ...workflow, resource: { graph: { nodes, edges: [] } } }, "https://example.test");
    const context = section(doc, "context");
    expect(context.nodes).toHaveLength(50);
    expect(context.omitted_node_count).toBe(70);
    expect(context.node_state_counts).toEqual({ not_run: 120 });
    expect(section(doc, "state")).toMatchObject({ node_count: 120, last_run: null });
    expect(doc).not.toContain("omit me");
  });

  it("distinguishes an unavailable resolver from an empty graph", () => {
    const doc = referenceSnapshotDocument({ ...workflow, resource: { resolver_status: "unavailable" } }, "https://example.test");
    expect(section(doc, "state")).toMatchObject({ resolver_status: "unavailable", node_count: null });
    const empty = referenceSnapshotDocument({ ...workflow, resource: { graph: { nodes: [], edges: [] }, last_run: null } }, "https://example.test");
    expect(section(empty, "state")).toMatchObject({ node_count: 0, last_run: null });
  });
});
