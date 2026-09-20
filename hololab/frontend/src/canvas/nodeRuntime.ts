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
// Aggregation rules (matches operator intuition — one bad shard poisons
// the whole node):
//
//   1. Any ``failed`` job → node state ``failed``.
//   2. Else any job still ``running`` / ``assigned`` / ``pending`` →
//      node state ``running`` (something is in flight).
//   3. Else all jobs ``done`` → node state ``done``.
//   4. Else fall back to the first non-done state — covers ``cancelled``
//      / ``orphaned`` mixes that don't fit the happy path.
//
// Progress for a fan-out node is ``done_shards / total_shards``. For a
// single-job node it's the job's own progress. fail_reason is inherited
// from the first failed job when the aggregate is ``failed``.

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
  if (hasFailed) return "failed";
  if (hasInFlight) return "running";
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
  // For the failed aggregate we surface the first failed job (so the
  // status tooltip shows that failure's reason). Otherwise use the
  // oldest job — for a fan-out the parent is created before its shards
  // and list_by_snapshot orders by created_ts, so index 0 is the parent
  // and runtime.job_id points at the coordinating job (matters for the
  // run-log affordance). Callers relying on job_id for artifact
  // resolution go through the backend's get_job_at, which also prefers
  // parent.
  const failed = jobs.find((j) => j.state === "failed");
  if (failed) return failed;
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
    const failedJob = state === "failed" ? gjs.find((j) => j.state === "failed") : undefined;
    out[gnid] = {
      state,
      progress: aggregateProgress(gjs),
      fail_reason: failedJob?.fail_reason ?? null,
      job_id: rep.job_id,
    };
  }
  return out;
}
