// Aggregate a snapshot's jobs into one NodeRuntime per graph_node_id.
//
// Why this exists
// ---------------
//
// A single graph node can produce more than one job row in a snapshot:
//
//   * arrayed<T> fan-out — one parent job + N shard jobs, all attributed
//     to the same graph_node_id in snapshot_jobs.
//   * rerun-from-node — inherits a prior job row and mints a new one on
//     top for the target slot.
//
// A naive ``Map.set(graph_node_id, job)`` loop drops N-1 of them and the
// last-write-wins outcome depends on job iteration order — for a fan-out
// where one shard failed and the rest succeeded, whether the node's
// status dot goes red or green becomes a race with SQLite's row ordering.
//
// Aggregation rules — "live progress trumps stale failure"
// --------------------------------------------------------
//
// Priority order (first match wins):
//
//   1. Any job still ``running`` / ``assigned`` / ``pending`` →
//      node state ``running``. Something is in flight, so the aggregate
//      is NOT a terminal verdict yet; showing red would panic the user
//      into thinking a run they just kicked off is already dead when
//      really it's 2/100 and climbing. Failed shards from earlier in
//      the same fan-out are still reported via ``fail_reason`` on the
//      first failed job, but they don't override live progress.
//   2. Else any job ``failed`` → node state ``failed`` (terminal, at
//      least one shard died and nothing is running to redeem it).
//   3. Else all jobs ``done`` → node state ``done``.
//   4. Else fall back to the first non-done state — covers ``cancelled``
//      / ``orphaned`` mixes that don't fit the happy path.
//
// This inverts the earlier "any-failed → failed" short-circuit. Motivation
// (2026-09-21): a fan-out with 8 shards failed + 3 running + 96 pending
// was rendering the node card red before the run had even settled, so
// operators watching a fresh dispatch saw "红色 = 完蛋" while the progress
// footer was ticking ``2/100``. Prioritising in-flight preserves the
// terminal-failed signal for terminated runs (nothing running → failed
// wins as before) while keeping the live view honest about progress.
//
// Progress for a fan-out node is ``done_shards / total_shards``. For a
// single-job node it's the job's own progress. fail_reason is inherited
// from the first failed job whenever any shard failed — surfaced through
// the status-dot tooltip even when the aggregate state is ``running``.

import type { SnapshotJob } from "../wire";
import type { NodeRuntime } from "./AlgorithmNode";

const IN_FLIGHT_STATES = new Set(["running", "assigned", "pending"]);

function aggregateStates(jobs: readonly SnapshotJob[]): string {
  let hasFailed = false;
  let hasInFlight = false;
  let firstNonDone: string | null = null;
  let allDone = true;
  for (const j of jobs) {
    if (j.state !== "done") allDone = false;
    if (j.state === "failed") hasFailed = true;
    else if (IN_FLIGHT_STATES.has(j.state)) hasInFlight = true;
    if (j.state !== "done" && firstNonDone === null) firstNonDone = j.state;
  }
  if (hasInFlight) return "running";
  if (hasFailed) return "failed";
  if (allDone) return "done";
  return firstNonDone ?? "done";
}

function aggregateProgress(
  jobs: readonly SnapshotJob[],
): { current: number; total: number } | null {
  if (jobs.length === 1) {
    // A lone fan-out parent (no shard rows have arrived yet, or the
    // snapshot pre-dates the shards' pending frames) still carries the
    // planned shard count on ``expected_shards``. Render 0/N immediately
    // instead of the parent's own null/1 progress so the node footer
    // shows "0/100" from the very first pending frame.
    const only = jobs[0];
    if (only.expected_shards != null && only.expected_shards > 0) {
      return { current: 0, total: only.expected_shards };
    }
    return only.progress ?? null;
  }
  // Fan-out: some jobs carry parent_job_id (they are shards). Count only
  // shards so a 100-frame fan-out shows N/100, not (N+1)/(N+2). The parent
  // coordinator job is excluded; its "done" state is not a shard completion.
  const shards = jobs.filter((j) => j.parent_job_id != null);
  const counted = shards.length > 0 ? shards : jobs;
  const done = counted.filter((j) => j.state === "done").length;
  // Prefer the parent's planned shard count over the row-count of already-
  // created shards. Fan-out is lazy: shard N+1 is only created after shard
  // N completes, so ``counted.length`` grows with progress and would show
  // a moving denominator (``20/22 → 21/23 → … → 101/101``, the bug this
  // field was introduced to fix). ``expected_shards`` is set once at
  // fan-out start and is stable for the run's lifetime.
  const parent = jobs.find((j) => j.parent_job_id == null && j.expected_shards != null);
  const total =
    parent?.expected_shards != null && parent.expected_shards > 0
      ? parent.expected_shards
      : counted.length;
  return { current: done, total };
}

function pickRepresentativeJob(jobs: readonly SnapshotJob[]): SnapshotJob {
  // Oldest job — for a fan-out the parent is created before its shards
  // and list_by_snapshot orders by created_ts, so index 0 is the parent
  // and runtime.job_id points at the coordinating job (matters for the
  // run-log affordance). Callers relying on job_id for artifact
  // resolution go through the backend's get_job_at, which also prefers
  // parent. Under the previous failed-first priority this function
  // preferred the failed shard so the tooltip's ``fail_reason`` came from
  // it; the new priority pipes ``fail_reason`` through
  // ``aggregateJobsToRuntime`` directly (see below), so the representative
  // can stay on the parent regardless of state — the tooltip still shows
  // the first shard failure even when the aggregate is ``running``.
  return jobs[0];
}

export function aggregateJobsToRuntime(
  jobs: readonly SnapshotJob[],
): Record<string, NodeRuntime> {
  const grouped = new Map<string, SnapshotJob[]>();
  for (const j of jobs) {
    if (!j.graph_node_id) continue;
    const bucket = grouped.get(j.graph_node_id);
    if (bucket) bucket.push(j);
    else grouped.set(j.graph_node_id, [j]);
  }
  const out: Record<string, NodeRuntime> = {};
  for (const [gnid, gjs] of grouped) {
    const state = aggregateStates(gjs);
    const rep = pickRepresentativeJob(gjs);
    // Surface the first failed job's reason even when the aggregate is
    // ``running`` — the status-dot tooltip then reads "running · <reason>"
    // so the operator can still see that some earlier shard blew up
    // while the fan-out continues. Once the aggregate settles to
    // ``failed``, this is the same reason surfaced by the terminal
    // verdict.
    const failedJob = gjs.find((j) => j.state === "failed");
    out[gnid] = {
      state,
      progress: aggregateProgress(gjs),
      fail_reason: failedJob?.fail_reason ?? null,
      job_id: rep.job_id,
    };
  }
  return out;
}
