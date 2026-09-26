// Aggregate a snapshot's jobs into one NodeRuntime per graph_node_id.
//
// Why this exists
// ---------------
//
// A single graph node can produce more than one job row in a snapshot:
//
//   * arrayed<T> fan-out — one parent job + N shard jobs, all attributed
//     to the same graph_node_id in snapshot_jobs.
//   * rerun-from-node — dispatches a fresh top-level job for the same
//     slot. Under cd61033 the earlier attempt's parent stays attributed
//     to ``snapshot_jobs`` (attribution now happens at CREATE, not at
//     DONE) and its done shards attribute at completion, so both
//     generations end up in ``list_by_snapshot`` output.
//
// A naive ``Map.set(graph_node_id, job)`` loop drops N-1 of them and the
// last-write-wins outcome depends on job iteration order — for a fan-out
// where one shard failed and the rest succeeded, whether the node's
// status dot goes red or green becomes a race with SQLite's row ordering.
//
// Generation scoping — "the newest run is what the node card shows"
// -----------------------------------------------------------------
//
// All aggregators (state / progress / fail_reason / representative job)
// operate on a *single generation* — the newest top-level parent (by
// ``created_ts``) plus any shards that point at it via
// ``parent_job_id``. Motivation (2026-09-23): a tri graph_node with
// three fan-out attempts (an older cancelled run + a middle cancelled
// run + a live re-run) rendered the node card as ``cancelled`` and
// ``50/100`` while the newest run's parent was ``done`` and its 18/100
// shards were live — the RecentJobsPanel, which groups by
// ``parent_job_id``, showed the live numbers on the top group. Scoping
// aligns the two surfaces: the node card mirrors the panel's newest
// group, and older attempts don't overwrite fresh success.
//
// Within a single generation, the state priority is unchanged:
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
// from the first failed job in the newest generation — older
// generations' failures don't leak into a fresh run's tooltip.

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
  parent: SnapshotJob,
): { current: number; total: number } | null {
  // Caller passes a single generation: ``parent`` is the top-level job
  // (fan-out coordinator or lone non-fan-out row) and ``jobs`` contains
  // it plus its shards.
  const shards = jobs.filter((j) => j.parent_job_id != null);
  if (shards.length === 0) {
    // Non-fan-out job, or a fan-out parent whose shards haven't been
    // created yet. When ``expected_shards`` is planned, render 0/N right
    // away so the footer doesn't blip through the parent's own ``null``
    // or ``1/1`` before shards materialise.
    if (parent.expected_shards != null && parent.expected_shards > 0) {
      return { current: 0, total: parent.expected_shards };
    }
    return parent.progress ?? null;
  }
  // Element vs shard: with ``batch_size > 1`` one shard covers multiple
  // elements (its ``shard_element_ids`` list). The node card footer
  // shows element throughput, not the coalesced shard count — operators
  // want "47/100 frames done", not "6/13 batches done" (that number is
  // the internal parallelism, not the domain progress).
  //
  // Byte-identical for batch=1: when no shard carries ``shard_element_ids``
  // we take the pre-batching path — ``done`` counts shards and ``total``
  // is the parent's ``expected_shards`` (or ``shards.length`` fallback),
  // matching the display before this file learned about batching.
  const isBatched = shards.some(
    (s) => s.shard_element_ids != null && s.shard_element_ids.length > 1,
  );
  if (!isBatched) {
    const done = shards.filter((j) => j.state === "done").length;
    const total =
      parent.expected_shards != null && parent.expected_shards > 0
        ? parent.expected_shards
        : shards.length;
    return { current: done, total };
  }
  const elemPerShard = (s: SnapshotJob) =>
    s.shard_element_ids && s.shard_element_ids.length > 0
      ? s.shard_element_ids.length
      : 1;
  const done = shards
    .filter((j) => j.state === "done")
    .reduce((sum, s) => sum + elemPerShard(s), 0);
  const total = shards.reduce((sum, s) => sum + elemPerShard(s), 0);
  return { current: done, total };
}

function pickCurrentGeneration(jobs: readonly SnapshotJob[]): {
  scoped: SnapshotJob[];
  parent: SnapshotJob;
} {
  // Newest top-level job (parent for a fan-out, or the lone job for a
  // non-fan-out re-run) by ``created_ts``. Generation = that parent plus
  // any shards pointing at it via ``parent_job_id``. Non-fan-out
  // generations return just the parent (no shards).
  //
  // Rare fallback: if the input has no top-level rows (only shards
  // visible — shouldn't happen in practice because ``list_by_snapshot``
  // returns the parent via the ``snapshot_jobs`` bridge), pick the
  // newest job as an anchor so the aggregator has something to work
  // with. runtime.job_id / progress will still resolve; the numbers may
  // be off but the alternative is crashing.
  const topLevel = jobs.filter((j) => j.parent_job_id == null);
  const parent =
    topLevel.length > 0
      ? topLevel.reduce((a, b) => (b.created_ts > a.created_ts ? b : a))
      : jobs.reduce((a, b) => (b.created_ts > a.created_ts ? b : a));
  const shards = jobs.filter((j) => j.parent_job_id === parent.job_id);
  return {
    scoped: shards.length > 0 ? [parent, ...shards] : [parent],
    parent,
  };
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
    const { scoped, parent } = pickCurrentGeneration(gjs);
    const state = aggregateStates(scoped);
    // Surface the first failed job's reason even when the aggregate is
    // ``running`` — the status-dot tooltip then reads "running · <reason>"
    // so the operator can still see that some earlier shard blew up
    // while the fan-out continues. Once the aggregate settles to
    // ``failed``, this is the same reason surfaced by the terminal
    // verdict. Scoped to the newest generation so an older run's
    // failure doesn't leak into a fresh re-run's tooltip.
    const failedJob = scoped.find((j) => j.state === "failed");
    // Timing across the newest generation. ``started_ts`` = earliest start
    // (parent's is usually earliest, but shards can beat it on out-of-order
    // startup — same guard used by RecentJobsPanel.groupElapsed). Falls
    // back to created_ts per row so a not-yet-started job still contributes
    // *something* rather than sinking the min to Infinity. ``updated_ts``
    // = latest update; running=>tick-from-now, terminal=>last completion.
    let earliestStart = Infinity;
    let latestUpdate = 0;
    for (const j of scoped) {
      const s = j.started_ts ?? j.created_ts;
      if (s < earliestStart) earliestStart = s;
      if (j.updated_ts > latestUpdate) latestUpdate = j.updated_ts;
    }
    out[gnid] = {
      state,
      progress: aggregateProgress(scoped, parent),
      fail_reason: failedJob?.fail_reason ?? null,
      // Representative job is the newest generation's parent — the
      // run-log affordance opens the coordinator's stdout, and the
      // backend's ``get_job_at`` also prefers the parent for artifact
      // resolution, so the two agree on what "this node's job" means.
      job_id: parent.job_id,
      started_ts: isFinite(earliestStart) ? earliestStart : null,
      updated_ts: latestUpdate > 0 ? latestUpdate : null,
    };
  }
  return out;
}
