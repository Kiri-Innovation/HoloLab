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
import type { CatalogPack, GraphNode, SnapshotJob, WorkflowGraph } from "../wire";
import type { NodeRuntime } from "./AlgorithmNode";
import { computeStaleness, packDefaultsFromCatalog } from "./staleness";

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
    batch_size: over.batch_size ?? 1,
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
    shard_element_ids: over.shard_element_ids ?? null,
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

  it("batch_size drift on done node → self_dirty with 批处理大小 reason", () => {
    // Mirrors the parallelism drift branch. batch_size is structural
    // (changes the shard-count math) so a draft/snapshot mismatch must
    // surface as a self_dirty reason.
    const draft = mkGraph([mkNode({ id: "n", batch_size: 4 })]);
    const snap = mkGraph([mkNode({ id: "n", batch_size: 1 })]);
    const runtimes: Record<string, NodeRuntime> = { n: { state: "done" } };
    const s = computeStaleness(draft, snap, runtimes, []).n;
    expect(s?.kind).toBe("self_dirty");
    expect(s?.title).toContain("批处理大小");
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

// Motivating case (2026-09-21): after commit 6717ce9 the gateway merges
// manifest defaults into ``Job.params`` at dispatch, and the resulting
// snapshot ``graph.nodes[i].params`` carries the full merged dict.
// The canvas draft stays sparse — autosave writes only operator-touched
// keys. Without normalisation the compare fired "参数已改: max_width,
// start_frame, end_frame" on every cold load of a just-successfully-run
// workflow, because those keys live in the snapshot but not the draft.
describe("computeStaleness — manifest default-fill normalisation", () => {
  function mkPack(over: Partial<CatalogPack> & { name: string; version: string; params: CatalogPack["params"] }): CatalogPack {
    return {
      name: over.name,
      version: over.version,
      manifest_hash: over.manifest_hash ?? "h",
      node_ids: [],
      description: null,
      category: [],
      docs: null,
      source_entry: null,
      manifest_path: null,
      source_dir: null,
      inputs: {},
      outputs: {},
      params: over.params,
      arrayable: false,
    };
  }

  it("sparse draft vs dispatch-merged snapshot (all defaults) → no self_dirty", () => {
    // Draft has only the operator-touched keys; snapshot has those plus
    // every manifest default (backend merges at dispatch). The two
    // must compare equal — the merged snapshot values ARE the
    // manifest defaults, so a rerun from the sparse draft would
    // produce bit-for-bit the same command line.
    const draft = mkGraph([
      mkNode({ id: "fx", algorithm_name: "frame-extraction", params: { max_frames: 100, skip: 0 } }),
    ]);
    const snap = mkGraph([
      mkNode({
        id: "fx",
        algorithm_name: "frame-extraction",
        params: { max_frames: 100, skip: 0, max_width: 0, start_frame: 0, end_frame: 0 },
      }),
    ]);
    const runtimes: Record<string, NodeRuntime> = { fx: { state: "done" } };
    const catalog = new Map<string, CatalogPack>([
      [
        "frame-extraction@0.1.0",
        mkPack({
          name: "frame-extraction",
          version: "0.1.0",
          params: {
            max_frames: { type: "int", default: 50, description: null, optional: true },
            skip: { type: "int", default: 1, description: null, optional: true },
            max_width: { type: "int", default: 0, description: null, optional: true },
            start_frame: { type: "int", default: 0, description: null, optional: true },
            end_frame: { type: "int", default: 0, description: null, optional: true },
          },
        }),
      ],
    ]);
    const out = computeStaleness(draft, snap, runtimes, [], packDefaultsFromCatalog(catalog));
    expect(out.fx).toBeNull();
  });

  it("real override differing from default → self_dirty (draft = {} merges to default 500; snap has 50)", () => {
    // The counter-case: draft is empty, snap has iterations=50. When
    // we merge the draft with defaults it becomes iterations=500;
    // that DOES differ from the snapshot's 50, so we must still
    // flag it. Guards against the fix over-hiding.
    const draft = mkGraph([mkNode({ id: "stg", algorithm_name: "stg-train", params: {} })]);
    const snap = mkGraph([
      mkNode({ id: "stg", algorithm_name: "stg-train", params: { iterations: 50 } }),
    ]);
    const runtimes: Record<string, NodeRuntime> = { stg: { state: "done" } };
    const catalog = new Map<string, CatalogPack>([
      [
        "stg-train@0.1.0",
        mkPack({
          name: "stg-train",
          version: "0.1.0",
          params: { iterations: { type: "int", default: 500, description: null, optional: true } },
        }),
      ],
    ]);
    const out = computeStaleness(draft, snap, runtimes, [], packDefaultsFromCatalog(catalog));
    expect(out.stg?.kind).toBe("self_dirty");
    expect(out.stg?.title).toContain("iterations");
  });

  it("missing pack in catalog → falls back to raw compare (no crash)", () => {
    // A pack the catalog hasn't hydrated yet: defaults resolve to
    // ``{}`` and the compare degrades to the pre-normalisation
    // behaviour. Better a legacy false-positive than a false-negative
    // that hides a real edit.
    const draft = mkGraph([mkNode({ id: "n", params: { a: 1 } })]);
    const snap = mkGraph([mkNode({ id: "n", params: { a: 1, b: 2 } })]);
    const runtimes: Record<string, NodeRuntime> = { n: { state: "done" } };
    const emptyCatalog = new Map<string, CatalogPack>();
    const out = computeStaleness(draft, snap, runtimes, [], packDefaultsFromCatalog(emptyCatalog));
    expect(out.n?.kind).toBe("self_dirty");
    expect(out.n?.title).toContain("b");
  });

  it("inflight_old_params also normalises against dispatch-merged Job.params", () => {
    // Dispatch merges defaults into Job.params (execution.py). If the
    // draft is sparse and the operator hasn't touched anything, the
    // "running job uses OLD params" chip must stay silent.
    const draft = mkGraph([mkNode({ id: "fx", algorithm_name: "frame-extraction", params: { max_frames: 100 } })]);
    const snap = mkGraph([mkNode({ id: "fx", algorithm_name: "frame-extraction", params: { max_frames: 100 } })]);
    const runtimes: Record<string, NodeRuntime> = { fx: { state: "running" } };
    const jobs: SnapshotJob[] = [
      mkJob({
        job_id: "j",
        graph_node_id: "fx",
        algorithm_name: "frame-extraction",
        state: "running",
        params: { max_frames: 100, skip: 1, max_width: 0, start_frame: 0, end_frame: 0 },
      }),
    ];
    const catalog = new Map<string, CatalogPack>([
      [
        "frame-extraction@0.1.0",
        mkPack({
          name: "frame-extraction",
          version: "0.1.0",
          params: {
            max_frames: { type: "int", default: 50, description: null, optional: true },
            skip: { type: "int", default: 1, description: null, optional: true },
            max_width: { type: "int", default: 0, description: null, optional: true },
            start_frame: { type: "int", default: 0, description: null, optional: true },
            end_frame: { type: "int", default: 0, description: null, optional: true },
          },
        }),
      ],
    ]);
    const out = computeStaleness(draft, snap, runtimes, jobs, packDefaultsFromCatalog(catalog));
    expect(out.fx).toBeNull();
  });
});
