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
  // Scope to the newest generation. Under rerun-from-node a graph_node_id
  // accumulates multiple top-level parents in a single snapshot — the
  // cancelled attempts stay in ``snapshot_jobs`` (attributed at creation
  // per cd61033) and their done shards attribute at completion — so a
  // naive filter would sum done-shards across every generation. Concrete
  // case: a fan-out with 32 done shards from an earlier cancelled run
  // plus 18 done from the live re-run rendered ``50/100`` on the node
  // card while the RecentJobsPanel (which groups by ``parent_job_id``)
  // showed the live ``18/100`` — the two views for the same node
  // disagreed by exactly the older run's done count.
  //
  // Fix: pick the newest top-level job (parent for a fan-out, or the
  // lone job for a non-fan-out re-run) and count only its shards. This
  // matches the panel's per-parent grouping so both surfaces agree.
  const topLevel = jobs.filter((j) => j.parent_job_id == null);
  if (topLevel.length === 0) {
    // Only shards visible — shouldn't happen in practice because
    // ``list_by_snapshot`` always returns the parent via the
    // ``snapshot_jobs`` bridge. Fall back to counting what we have.
    const done = jobs.filter((j) => j.state === "done").length;
    return { current: done, total: jobs.length };
  }
  const current = topLevel.reduce((a, b) =>
    b.created_ts > a.created_ts ? b : a,
  );
  const shards = jobs.filter((j) => j.parent_job_id === current.job_id);
  if (shards.length === 0) {
    // Non-fan-out job, or a fan-out parent whose shards haven't been
    // created yet. When ``expected_shards`` is planned, render 0/N right
    // away so the footer doesn't blip through the parent's own ``null``
    // or ``1/1`` before shards materialise.
    if (current.expected_shards != null && current.expected_shards > 0) {
      return { current: 0, total: current.expected_shards };
    }
    return current.progress ?? null;
  }
  const done = shards.filter((j) => j.state === "done").length;
  // Prefer the parent's planned shard count over the row-count of already-
  // created shards. Even though shards are pre-created upfront (2026-09-21
  // refactor), ``expected_shards`` is still the authoritative denominator:
  // it's frozen at fan-out start and can't drift if row-creation partially
  // fails.
  const total =
    current.expected_shards != null && current.expected_shards > 0
      ? current.expected_shards
      : shards.length;
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
