// Unit coverage for the ``inflight_old_params`` branch that closes the
// "running job is using OLD params" invisibility gap. See
// canvas/staleness.ts for the motivating incident (2026-09-21).
//
// The pre-existing self_dirty / upstream_dirty branches are already
// exercised end-to-end through the App + canvas rendering path and via
// the diffGraphs sibling; this file focuses on the new branch and the
// rule that in-flight-with-matched-params still returns ``null`` (i.e.,
// we didn't accidentally make every running node loud).

import { describe, expect, it } from "vitest";
import type { GraphNode, SnapshotJob, WorkflowGraph } from "../wire";
import type { NodeRuntime } from "./AlgorithmNode";
import { computeStaleness } from "./staleness";

function mkNode(over: Partial<GraphNode> & { id: string }): GraphNode {
  return {
    id: over.id,
    algorithm_name: over.algorithm_name ?? "alg",
    algorithm_version: over.algorithm_version ?? "0.1.0",
    position: over.position ?? { x: 0, y: 0 },
    params: over.params ?? {},
    assigned_node_id: over.assigned_node_id ?? null,
    arrayed_toggle: over.arrayed_toggle ?? false,
    parallelism: over.parallelism ?? 1,
  };
}

function mkGraph(nodes: GraphNode[]): WorkflowGraph {
  return { nodes, edges: [] };
}

function mkJob(over: Partial<SnapshotJob> & { job_id: string; graph_node_id: string }): SnapshotJob {
  return {
    job_id: over.job_id,
    workflow_id: "w",
    node_id: null,
    graph_node_id: over.graph_node_id,
    algorithm_name: over.algorithm_name ?? "alg",
    algorithm_version: over.algorithm_version ?? "0.1.0",
    state: over.state ?? "running",
    progress: over.progress ?? null,
    fail_reason: null,
    fail_exit_code: null,
    fail_message: null,
    params: over.params ?? {},
    input_handles: {},
    output_handles: null,
    parent_job_id: over.parent_job_id ?? null,
    shard_element_id: over.shard_element_id ?? null,
    expected_shards: over.expected_shards ?? null,
    created_ts: over.created_ts ?? 0,
    updated_ts: over.updated_ts ?? 0,
  };
}

describe("computeStaleness — inflight_old_params", () => {
  it("running job with drifted params → inflight_old_params with reason + job id", () => {
    // Motivating case: draft ``use_gpu=1`` while the still-running job
    // was dispatched with ``use_gpu=0``. Chip must call out the
    // specific param, the job's short-hash, and the "重新运行" hint so
    // the operator has a next-step, not just a warning.
    const draft = mkGraph([mkNode({ id: "n", params: { use_gpu: 1 } })]);
    const snap = mkGraph([mkNode({ id: "n", params: { use_gpu: 0 } })]);
    const runtimes: Record<string, NodeRuntime> = { n: { state: "running" } };
    const jobs: SnapshotJob[] = [
      mkJob({ job_id: "abcdef1234", graph_node_id: "n", state: "running", params: { use_gpu: 0 } }),
    ];

    const out = computeStaleness(draft, snap, runtimes, jobs);
    expect(out.n).not.toBeNull();
    expect(out.n?.kind).toBe("inflight_old_params");
    expect(out.n?.title).toContain("旧参数");
    expect(out.n?.title).toContain("use_gpu");
    expect(out.n?.title).toContain("job abcdef1");
    expect(out.n?.title).toContain("完成后");
  });

  it("running job with matching params → null (no noise while in-flight)", () => {
    // The status dot alone conveys the state; we must NOT paint every
    // running node with an ``!旧参数`` chip.
    const draft = mkGraph([mkNode({ id: "n", params: { use_gpu: 1 } })]);
    const snap = mkGraph([mkNode({ id: "n", params: { use_gpu: 1 } })]);
    const runtimes: Record<string, NodeRuntime> = { n: { state: "running" } };
    const jobs: SnapshotJob[] = [
      mkJob({ job_id: "j", graph_node_id: "n", state: "running", params: { use_gpu: 1 } }),
    ];
    expect(computeStaleness(draft, snap, runtimes, jobs).n).toBeNull();
  });

  it("running job with drifted algorithm version → chip flags the algo change", () => {
    const draft = mkGraph([mkNode({ id: "n", algorithm_version: "0.2.0" })]);
    const snap = mkGraph([mkNode({ id: "n", algorithm_version: "0.1.0" })]);
    const runtimes: Record<string, NodeRuntime> = { n: { state: "running" } };
    const jobs: SnapshotJob[] = [
      mkJob({ job_id: "j", graph_node_id: "n", state: "running", algorithm_version: "0.1.0" }),
    ];
    const s = computeStaleness(draft, snap, runtimes, jobs).n;
    expect(s?.kind).toBe("inflight_old_params");
    expect(s?.title).toContain("算法已更改");
  });

  it("fan-out parent + shards with drift → drift detected against oldest (parent) job", () => {
    // Fan-outs share params across shards but the parent is oldest;
    // oldestInFlightJob picks the parent, which is what we want so the
    // chip's job short-hash matches the run-log coordinator entry.
    const draft = mkGraph([mkNode({ id: "n", params: { use_gpu: 1 } })]);
    const snap = mkGraph([mkNode({ id: "n", params: { use_gpu: 0 } })]);
    const runtimes: Record<string, NodeRuntime> = { n: { state: "running" } };
    const jobs: SnapshotJob[] = [
      mkJob({ job_id: "parent01", graph_node_id: "n", state: "running", params: { use_gpu: 0 }, created_ts: 0 }),
      mkJob({ job_id: "shard999", graph_node_id: "n", state: "running", params: { use_gpu: 0 }, parent_job_id: "parent01", created_ts: 5 }),
    ];
    const s = computeStaleness(draft, snap, runtimes, jobs).n;
    expect(s?.kind).toBe("inflight_old_params");
    expect(s?.title).toContain("job parent");
  });

  it("running with no matching job in the array → returns null (no crash on empty)", () => {
    // Ref-count mismatch or a race between the aggregator (says
    // ``running``) and the raw job list (still empty): the compute
    // must fail gracefully to ``null`` rather than throw or emit a
    // chip against phantom params.
    const draft = mkGraph([mkNode({ id: "n", params: { use_gpu: 1 } })]);
    const runtimes: Record<string, NodeRuntime> = { n: { state: "running" } };
    expect(computeStaleness(draft, null, runtimes, []).n).toBeNull();
  });

  it("done node with drift → self_dirty (unchanged behaviour, not the new chip)", () => {
    // Guards against the refactor accidentally re-routing the done
    // case through the new branch — the amber dot is the correct
    // (quiet) tone for "edited but nothing in flight".
    const draft = mkGraph([mkNode({ id: "n", params: { use_gpu: 1 } })]);
    const snap = mkGraph([mkNode({ id: "n", params: { use_gpu: 0 } })]);
    const runtimes: Record<string, NodeRuntime> = { n: { state: "done" } };
    const jobs: SnapshotJob[] = [
      mkJob({ job_id: "j", graph_node_id: "n", state: "done", params: { use_gpu: 0 } }),
    ];
    const s = computeStaleness(draft, snap, runtimes, jobs).n;
    expect(s?.kind).toBe("self_dirty");
    expect(s?.title).toContain("use_gpu");
  });

  it("computeStaleness works when snapshotJobs arg is omitted (back-compat default)", () => {
    // The signature added ``snapshotJobs`` as an optional trailing
    // argument; existing 3-arg call sites must keep compiling and
    // producing identical results.
    const draft = mkGraph([mkNode({ id: "n", params: { use_gpu: 1 } })]);
    const snap = mkGraph([mkNode({ id: "n", params: { use_gpu: 0 } })]);
    const runtimes: Record<string, NodeRuntime> = { n: { state: "done" } };
    const s = computeStaleness(draft, snap, runtimes).n;
    expect(s?.kind).toBe("self_dirty");
  });
});
