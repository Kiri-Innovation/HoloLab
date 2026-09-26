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
    shard_element_ids: over.shard_element_ids ?? null,
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

  it("batched fan-out: progress counts elements, not coalesced shards", () => {
    // batch_size=8 over 100 elements → 13 shards (12 full + 1 remainder).
    // After 6 shards finish we expect ``47/100`` (6*8 - 1 for the last
    // partial batch — here every done shard is a full 8, so 48/100 is
    // exact) at the node card, NOT ``6/13``. The user-visible number
    // is the domain progress (frames processed), never the internal
    // parallelism.
    const parent = mkJob({
      job_id: "p",
      state: "running",
      expected_shards: 13,
      created_ts: 0,
    });
    // Build 13 shards: first 6 done (each covers 8 elements),
    // 7 pending (6 full batches of 8 + 1 remainder of 4).
    const shards: SnapshotJob[] = [];
    let created = 1;
    for (let i = 0; i < 6; i++) {
      shards.push(
        mkJob({
          job_id: `s${i}`,
          parent_job_id: "p",
          state: "done",
          shard_element_ids: Array.from(
            { length: 8 },
            (_, k) => `elem_${i * 8 + k}`,
          ),
          created_ts: created++,
        }),
      );
    }
    for (let i = 6; i < 12; i++) {
      shards.push(
        mkJob({
          job_id: `s${i}`,
          parent_job_id: "p",
          state: "pending",
          shard_element_ids: Array.from(
            { length: 8 },
            (_, k) => `elem_${i * 8 + k}`,
          ),
          created_ts: created++,
        }),
      );
    }
    // Trailing remainder batch — 4 elements, batch_size=8 but only 4 left.
    shards.push(
      mkJob({
        job_id: "s12",
        parent_job_id: "p",
        state: "pending",
        shard_element_ids: ["elem_96", "elem_97", "elem_98", "elem_99"],
        created_ts: created++,
      }),
    );

    const out = aggregateJobsToRuntime([parent, ...shards]);
    expect(out.n.state).toBe("running");
    expect(out.n.progress).toEqual({ current: 48, total: 100 });
  });

  it("batch_size=1 fan-out is byte-identical to pre-batching aggregate", () => {
    // Guard the byte-identical contract: with batch_size=1 every shard
    // has ``shard_element_ids == null`` and the aggregator must fall
    // back to the shard-count-based total (parent's ``expected_shards``
    // when set), matching the exact display users saw before this file
    // learned about batching.
    const parent = mkJob({
      job_id: "p",
      state: "running",
      expected_shards: 13,
      created_ts: 0,
    });
    const shards = Array.from({ length: 13 }, (_, i) =>
      mkJob({
        job_id: `s${i}`,
        parent_job_id: "p",
        state: i < 6 ? "done" : "pending",
        shard_element_id: `elem_${i}`,
        // Deliberately null — batch=1 leaves this field NULL per the
        // backend's execution.py contract.
        shard_element_ids: null,
        created_ts: 1 + i,
      }),
    );
    const out = aggregateJobsToRuntime([parent, ...shards]);
    expect(out.n.progress).toEqual({ current: 6, total: 13 });
  });

  it("batched fan-out: partial done shards count only their own elements", () => {
    // Mixed states: 2 done full batches + 1 done partial batch + rest
    // pending. Elements done = 8 + 8 + 3 = 19.
    const parent = mkJob({
      job_id: "p",
      state: "running",
      expected_shards: 5,
      created_ts: 0,
    });
    const s0 = mkJob({
      job_id: "s0",
      parent_job_id: "p",
      state: "done",
      shard_element_ids: ["a", "b", "c", "d", "e", "f", "g", "h"],
      created_ts: 1,
    });
    const s1 = mkJob({
      job_id: "s1",
      parent_job_id: "p",
      state: "done",
      shard_element_ids: ["i", "j", "k", "l", "m", "n", "o", "p"],
      created_ts: 2,
    });
    const s2 = mkJob({
      job_id: "s2",
      parent_job_id: "p",
      state: "done",
      shard_element_ids: ["q", "r", "s"],
      created_ts: 3,
    });
    const s3 = mkJob({
      job_id: "s3",
      parent_job_id: "p",
      state: "pending",
      shard_element_ids: ["t", "u", "v", "w", "x", "y", "z", "aa"],
      created_ts: 4,
    });
    const s4 = mkJob({
      job_id: "s4",
      parent_job_id: "p",
      state: "pending",
      shard_element_ids: ["ab", "ac", "ad"],
      created_ts: 5,
    });
    const out = aggregateJobsToRuntime([parent, s0, s1, s2, s3, s4]);
    // Total elements = 8+8+3+8+3 = 30; done = 8+8+3 = 19.
    expect(out.n.progress).toEqual({ current: 19, total: 30 });
  });

  it("parent FAILED with shards stranded PENDING → aggregate stays failed (no clock tick)", () => {
    // 2026-09-26 incident: tri fan-out with parent FAILED at t+8m47s
    // but 5 shards stranded PENDING (execution.py stale-reader race).
    // Under the old rule ``hasInFlight`` (pending shards) beat
    // ``hasFailed``, so the aggregate came back ``running`` and the
    // card's elapsed clock ticked ``Date.now() - started_ts`` forever
    // (user saw "1h 24m" and rising until refresh). Parent-terminal
    // must win — the fan-out is over, stranded shards notwithstanding.
    const parent = mkJob({
      job_id: "p",
      state: "failed",
      fail_reason: "shard 0 finished pending; shard 1 finished pending; …",
      expected_shards: 100,
      created_ts: 0,
      updated_ts: 527,
      started_ts: 0,
    });
    const s0Pending = mkJob({
      job_id: "s0",
      parent_job_id: "p",
      state: "pending",
      created_ts: 1,
      updated_ts: 1,
    });
    const s1Pending = mkJob({
      job_id: "s1",
      parent_job_id: "p",
      state: "pending",
      created_ts: 2,
      updated_ts: 2,
    });
    const s2Done = mkJob({
      job_id: "s2",
      parent_job_id: "p",
      state: "done",
      created_ts: 3,
      updated_ts: 10,
    });
    const out = aggregateJobsToRuntime([parent, s0Pending, s1Pending, s2Done]);
    expect(out.n.state).toBe("failed");
    // Timing frozen at the parent's terminal moment, not now: the
    // FooterRunSummary branch on ``state === "running"`` won't fire,
    // so the fixed ``formatCardDuration(started_ts, updated_ts)``
    // renders instead. ``updated_ts`` is the max across the scoped
    // rows — parent's fail moment (527) dominates the done shard's
    // earlier finish (10).
    expect(out.n.updated_ts).toBe(527);
  });

  it("parent cancelled with stray in-flight shard → aggregate is cancelled", () => {
    // Same short-circuit for the cancel path — if the coordinator has
    // been cancelled, any leftover running/pending shard is a straggler
    // that will be reaped by cascade; the card should read cancelled,
    // not running.
    const parent = mkJob({
      job_id: "p",
      state: "cancelled",
      expected_shards: 4,
      created_ts: 0,
    });
    const running = mkJob({
      job_id: "s0",
      parent_job_id: "p",
      state: "running",
      created_ts: 1,
    });
    const out = aggregateJobsToRuntime([parent, running]);
    expect(out.n.state).toBe("cancelled");
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
