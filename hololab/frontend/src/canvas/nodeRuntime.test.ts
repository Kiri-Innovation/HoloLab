// Aggregation priority tests. The load-bearing rule this file locks in:
//
//   in-flight (running / assigned / pending) beats failed
//
// Motivation: a fan-out with one or two early shard failures kept
// panicking the canvas red while the remaining 90+ shards were still
// running. Operators kicked off a fresh dispatch, saw ``2/100`` in the
// footer, and simultaneously a red border screaming "failed" — they
// thought their new run had already died. The invariant below defends
// against that regressing back to the old "any failed → red" priority.

import { describe, expect, it } from "vitest";
import type { SnapshotJob } from "../wire";
import { aggregateJobsToRuntime } from "./nodeRuntime";

function mkJob(over: Partial<SnapshotJob>): SnapshotJob {
  return {
    job_id: over.job_id ?? "j",
    workflow_id: "w",
    node_id: null,
    graph_node_id: over.graph_node_id ?? "n",
    algorithm_name: "alg",
    algorithm_version: "0.1.0",
    state: over.state ?? "pending",
    progress: over.progress ?? null,
    fail_reason: over.fail_reason ?? null,
    fail_exit_code: null,
    fail_message: null,
    params: {},
    input_handles: {},
    output_handles: null,
    parent_job_id: over.parent_job_id ?? null,
    shard_element_id: over.shard_element_id ?? null,
    expected_shards: over.expected_shards ?? null,
    created_ts: over.created_ts ?? 0,
    updated_ts: over.updated_ts ?? 0,
  };
}

describe("aggregateJobsToRuntime — state priority", () => {
  it("in-flight jobs override failed shards (live progress trumps stale failure)", () => {
    // The canonical bug shape: fan-out with the parent + one early
    // failure + shards still running / pending. Aggregate must be
    // ``running`` so the canvas dot stays live and the fresh dispatch
    // isn't misread as dead.
    const parent = mkJob({
      job_id: "p",
      state: "running",
      expected_shards: 100,
      created_ts: 0,
    });
    const s0Failed = mkJob({
      job_id: "s0",
      parent_job_id: "p",
      state: "failed",
      fail_reason: "shard 0 blew up",
      created_ts: 1,
    });
    const s1Running = mkJob({
      job_id: "s1",
      parent_job_id: "p",
      state: "running",
      created_ts: 2,
    });
    const s2Pending = mkJob({
      job_id: "s2",
      parent_job_id: "p",
      state: "pending",
      created_ts: 3,
    });
    const s3Done = mkJob({
      job_id: "s3",
      parent_job_id: "p",
      state: "done",
      created_ts: 4,
    });

    const out = aggregateJobsToRuntime([parent, s0Failed, s1Running, s2Pending, s3Done]);
    expect(out.n.state).toBe("running");
    // fail_reason is still surfaced so the status-dot tooltip can show
    // "running · shard 0 blew up" — the failure hasn't disappeared,
    // it just doesn't drive the border colour.
    expect(out.n.fail_reason).toBe("shard 0 blew up");
    // Progress: one shard done out of the parent's planned 100.
    expect(out.n.progress).toEqual({ current: 1, total: 100 });
    // Representative job is the parent (oldest, index 0 in
    // created_ts-sorted order) — matters for the run-log affordance,
    // which should target the coordinator job, not the failed shard.
    expect(out.n.job_id).toBe("p");
  });

  it("assigned + failed → running (assigned counts as in-flight)", () => {
    const parent = mkJob({ job_id: "p", state: "running", created_ts: 0 });
    const s0 = mkJob({
      job_id: "s0",
      parent_job_id: "p",
      state: "failed",
      created_ts: 1,
    });
    const s1 = mkJob({
      job_id: "s1",
      parent_job_id: "p",
      state: "assigned",
      created_ts: 2,
    });
    const out = aggregateJobsToRuntime([parent, s0, s1]);
    expect(out.n.state).toBe("running");
  });

  it("pending-only + failed → running (pending still counts as in-flight)", () => {
    // Right after dispatch — no shard has picked up yet but the
    // fan-out is committed. This must not paint the node red on the
    // strength of a prior-attempt failure that leaked into the list.
    const parent = mkJob({ job_id: "p", state: "pending", created_ts: 0 });
    const s0 = mkJob({
      job_id: "s0",
      parent_job_id: "p",
      state: "failed",
      created_ts: 1,
    });
    const s1 = mkJob({
      job_id: "s1",
      parent_job_id: "p",
      state: "pending",
      created_ts: 2,
    });
    const out = aggregateJobsToRuntime([parent, s0, s1]);
    expect(out.n.state).toBe("running");
  });

  it("all terminal, some failed → failed (terminal verdict once nothing runs)", () => {
    // Regression guard the OTHER way: after everything settles, we
    // still want the failed-shard-poisons-node rule so a dead run
    // reads as red. This is not a regression, it's the intent.
    //
    // Matches the real fan-out end-state: the parent's fail_reason is
    // the concatenated shard-failure summary written by
    // ``_mark_parent_failed`` in execution.py — we surface that (via
    // the ``find`` order, parent is first) rather than any single
    // shard's reason.
    const parent = mkJob({
      job_id: "p",
      state: "failed",
      fail_reason: "shard 1: boom",
      created_ts: 0,
    });
    const s0 = mkJob({
      job_id: "s0",
      parent_job_id: "p",
      state: "done",
      created_ts: 1,
    });
    const s1 = mkJob({
      job_id: "s1",
      parent_job_id: "p",
      state: "failed",
      fail_reason: "boom",
      created_ts: 2,
    });
    const out = aggregateJobsToRuntime([parent, s0, s1]);
    expect(out.n.state).toBe("failed");
    expect(out.n.fail_reason).toBe("shard 1: boom");
  });

  it("all done → done", () => {
    const parent = mkJob({ job_id: "p", state: "done", created_ts: 0 });
    const s0 = mkJob({
      job_id: "s0",
      parent_job_id: "p",
      state: "done",
      created_ts: 1,
    });
    const s1 = mkJob({
      job_id: "s1",
      parent_job_id: "p",
      state: "done",
      created_ts: 2,
    });
    const out = aggregateJobsToRuntime([parent, s0, s1]);
    expect(out.n.state).toBe("done");
    expect(out.n.fail_reason).toBeNull();
  });

  it("single running job with no shards → running", () => {
    const only = mkJob({
      job_id: "solo",
      state: "running",
      progress: { current: 3, total: 10 },
    });
    const out = aggregateJobsToRuntime([only]);
    expect(out.n.state).toBe("running");
    expect(out.n.progress).toEqual({ current: 3, total: 10 });
  });

  it("cancelled + failed with nothing in flight → failed (terminal wins)", () => {
    // Cancelled is not in-flight, so we're in the terminal branch:
    // any failed still poisons the aggregate to red so an operator
    // sees the failure rather than a misleading "cancelled".
    const parent = mkJob({ job_id: "p", state: "cancelled", created_ts: 0 });
    const s0 = mkJob({
      job_id: "s0",
      parent_job_id: "p",
      state: "failed",
      created_ts: 1,
    });
    const s1 = mkJob({
      job_id: "s1",
      parent_job_id: "p",
      state: "cancelled",
      created_ts: 2,
    });
    const out = aggregateJobsToRuntime([parent, s0, s1]);
    expect(out.n.state).toBe("failed");
  });

  it("multi-generation fan-out (rerun-from-node) scopes progress to newest parent", () => {
    // Real bug shape (2026-09-23): a snapshot's ``tri`` graph_node
    // accumulated three fan-out attempts — an earlier cancelled run
    // (68 cancelled + 32 done shards), a middle cancelled run
    // (100 cancelled), and a live re-run at 18/100. The node card
    // rendered ``50/100`` (32 + 0 + 18 done shards summed across
    // generations, denominator borrowed from any one parent's
    // expected_shards) while the RecentJobsPanel — which groups by
    // parent_job_id — showed the live ``18/100`` for the newest group.
    // The two views disagreed by exactly the older runs' done count.
    // Fix: scope to the newest top-level parent (by created_ts) and
    // count only its shards.
    const oldParent = mkJob({
      job_id: "p_old",
      state: "cancelled",
      expected_shards: 100,
      created_ts: 0,
    });
    const oldDoneShard = mkJob({
      job_id: "s_old_done",
      parent_job_id: "p_old",
      state: "done",
      created_ts: 1,
    });
    const oldCancelledShard = mkJob({
      job_id: "s_old_cancel",
      parent_job_id: "p_old",
      state: "cancelled",
      created_ts: 2,
    });
    const newParent = mkJob({
      job_id: "p_new",
      state: "running",
      expected_shards: 100,
      created_ts: 100,
    });
    const newDoneShard = mkJob({
      job_id: "s_new_done",
      parent_job_id: "p_new",
      state: "done",
      created_ts: 101,
    });
    const newRunningShard = mkJob({
      job_id: "s_new_run",
      parent_job_id: "p_new",
      state: "running",
      created_ts: 102,
    });
    const out = aggregateJobsToRuntime([
      oldParent,
      oldDoneShard,
      oldCancelledShard,
      newParent,
      newDoneShard,
      newRunningShard,
    ]);
    // Only the newest parent's one done shard counts, denominator is
    // newParent.expected_shards. Old run's done shard is excluded.
    expect(out.n.progress).toEqual({ current: 1, total: 100 });
  });

  it("groups jobs by graph_node_id and aggregates each independently", () => {
    // Two distinct graph_nodes, each a small fan-out (parent + one
    // shard) so the aggregation stays intra-generation. A's shard is
    // failed but the parent is still running (in-flight wins → running).
    // B is terminal with one failed shard (terminal + failed → failed).
    const aParent = mkJob({ job_id: "a1", graph_node_id: "A", state: "running", created_ts: 0 });
    const aShard = mkJob({ job_id: "a2", graph_node_id: "A", parent_job_id: "a1", state: "failed", created_ts: 1 });
    const bParent = mkJob({ job_id: "b1", graph_node_id: "B", state: "failed", created_ts: 2 });
    const bShard = mkJob({ job_id: "b2", graph_node_id: "B", parent_job_id: "b1", state: "done", created_ts: 3 });
    const out = aggregateJobsToRuntime([aParent, aShard, bParent, bShard]);
    // A has an in-flight parent → running.
    expect(out.A.state).toBe("running");
    // B has no in-flight job → failed wins (terminal branch).
    expect(out.B.state).toBe("failed");
  });

  it("multi-generation: older cancelled run does not poison newest done run", () => {
    // Real bug (2026-09-23): a tri graph_node with an earlier
    // cancelled fan-out plus a newer fan-out that completed cleanly
    // rendered the node card as ``cancelled`` while the newest
    // parent said ``done``. Scope the state aggregate to the newest
    // generation so a fresh success isn't overwritten by stale
    // cancellations. fail_reason must also be null — the old run's
    // shard failures aren't relevant to the successful re-run.
    const oldParent = mkJob({
      job_id: "p_old",
      state: "cancelled",
      expected_shards: 100,
      created_ts: 0,
    });
    const oldCancelledShard = mkJob({
      job_id: "s_old",
      parent_job_id: "p_old",
      state: "cancelled",
      created_ts: 1,
    });
    const newParent = mkJob({
      job_id: "p_new",
      state: "done",
      expected_shards: 100,
      created_ts: 100,
    });
    const newDoneShard = mkJob({
      job_id: "s_new",
      parent_job_id: "p_new",
      state: "done",
      created_ts: 101,
    });
    const out = aggregateJobsToRuntime([
      oldParent,
      oldCancelledShard,
      newParent,
      newDoneShard,
    ]);
    expect(out.n.state).toBe("done");
    expect(out.n.job_id).toBe("p_new");
    expect(out.n.fail_reason).toBeNull();
    expect(out.n.progress).toEqual({ current: 1, total: 100 });
  });

  it("multi-generation: in-flight beats failed still applies across generations", () => {
    // The older run FAILED terminally; the newer run is currently
    // running. In-flight wins — but only because the newest generation
    // itself is in flight. The older gen's failure must not leak in as
    // fail_reason (it belongs to a different run entirely).
    const oldParent = mkJob({
      job_id: "p_old",
      state: "failed",
      fail_reason: "old boom",
      expected_shards: 10,
      created_ts: 0,
    });
    const oldFailedShard = mkJob({
      job_id: "s_old",
      parent_job_id: "p_old",
      state: "failed",
      fail_reason: "old shard boom",
      created_ts: 1,
    });
    const newParent = mkJob({
      job_id: "p_new",
      state: "running",
      expected_shards: 10,
      created_ts: 100,
    });
    const newRunningShard = mkJob({
      job_id: "s_new",
      parent_job_id: "p_new",
      state: "running",
      created_ts: 101,
    });
    const out = aggregateJobsToRuntime([
      oldParent,
      oldFailedShard,
      newParent,
      newRunningShard,
    ]);
    expect(out.n.state).toBe("running");
    expect(out.n.job_id).toBe("p_new");
    expect(out.n.fail_reason).toBeNull();
  });

  it("multi-generation: newest done run reports null fail_reason even if older gen failed", () => {
    const oldParent = mkJob({
      job_id: "p_old",
      state: "failed",
      fail_reason: "old boom",
      expected_shards: 5,
      created_ts: 0,
    });
    const oldFailedShard = mkJob({
      job_id: "s_old",
      parent_job_id: "p_old",
      state: "failed",
      fail_reason: "old shard boom",
      created_ts: 1,
    });
    const newParent = mkJob({
      job_id: "p_new",
      state: "done",
      expected_shards: 5,
      created_ts: 100,
    });
    const newDoneShard = mkJob({
      job_id: "s_new",
      parent_job_id: "p_new",
      state: "done",
      created_ts: 101,
    });
    const out = aggregateJobsToRuntime([
      oldParent,
      oldFailedShard,
      newParent,
      newDoneShard,
    ]);
    expect(out.n.state).toBe("done");
    expect(out.n.fail_reason).toBeNull();
    expect(out.n.progress).toEqual({ current: 1, total: 5 });
  });
});
