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

describe("run reference snapshot", () => {
  const run: ResolvedReference = {kind:"run", ref:"hololab://run/run-id", resource:{
    snapshot_id:"run-id", workflow_id:"wf", created_ts:200,
    graph:{nodes:[{id:"n",algorithm_name:"train",algorithm_version:"1",params:{secret:"omit"}}],edges:[]},
    jobs:[{job_id:"old-job",graph_node_id:"n",algorithm_name:"train",state:"done",started_ts:10,updated_ts:20,params:{secret:"omit"},input_handles:{huge:"omit"},log:"omit"}],
  }, related:{workflow:"/api/workflows/wf"}};
  it("uses a typed compact snapshot and deep navigation without invented lifecycle times", () => {
    const doc=referenceSnapshotDocument(run,"http://host:8828");
    expect(section(doc,"identity")).toEqual({kind:"external:hololab:run",ref:run.ref,run_id:"run-id"});
    expect(section(doc,"state")).toMatchObject({status:"done",job_count:1,created_ts:200,started_ts:null,ended_ts:null,trigger:null});
    expect(section(doc,"navigation").run_record).toBe("/api/snapshots/run-id");
    expect(section(doc,"context").jobs[0].job_id).toBe("old-job");
    expect(doc).not.toContain('"omit"');
  });
  it("bounds nodes and jobs and counts all states", () => {
    const doc=referenceSnapshotDocument({...run,resource:{...run.resource,jobs:Array.from({length:80},(_,i)=>({job_id:String(i),state:i===79?"failed":"done"}))}}, "http://host:8828");
    expect(section(doc,"state")).toMatchObject({status:"failed",job_count:80,state_counts:{done:79,failed:1}});
    expect(section(doc,"context").jobs).toHaveLength(30);
    expect(section(doc,"context").omitted_job_count).toBe(50);
  });
  it("marks missing resolvers as unknown, not an empty pending run", () => {
    const doc=referenceSnapshotDocument({...run,resource:{resolver_status:"unavailable"}}, "http://host:8828");
    expect(section(doc,"state")).toMatchObject({status:"unknown",job_count:null,resolver_status:"unavailable"});
  });
});
