// Compact recent-jobs list. Sits under the NodeInspector at the bottom of
// the layout. Clicking a row selects the corresponding canvas node so the
// user can jump from a runtime event to the node that produced it; the
// dedicated "view log" icon on the row opens the log viewer so a red dot
// no longer means "guess what went wrong". See JobLogModal.tsx.

import { useEffect, useRef, useState } from "react";
import type { NodeRuntime } from "./AlgorithmNode";
import { stateColour } from "./AlgorithmNode";
import { CopyRefButton } from "./CopyRefButton";
import { JobLogModal } from "./JobLogModal";

export interface RecentJobRow {
  job_id: string;
  algorithm_name: string;
  algorithm_version: string;
  state: string;
  graph_node_id: string | null;
  progress?: { current: number; total: number } | null;
  updated_ts: number;
  created_ts: number;
  started_ts?: number | null;
  // Fan-out linkage: shard rows carry these; parent + regular jobs leave
  // them null. Grouping uses ``parent_job_id`` to collapse a parent and
  // its shards into a single Recent Jobs entry.
  parent_job_id?: string | null;
  shard_element_id?: string | null;
  fail_reason?: string | null;
}

export interface RecentJobsPanelProps {
  jobs: RecentJobRow[];
  onSelectGraphNode: (graphNodeId: string) => void;
  currentWorkflowId: string | null;
}

const CARD: React.CSSProperties = {
  padding: "7px 14px",
  borderBottom: "1px solid var(--border)",
  fontSize: "var(--fs-sm)",
  color: "var(--text-body)",
  display: "grid",
  // dot · title · progress · elapsed · [log-btn] · [copy-ref]
  gridTemplateColumns: "10px 1fr auto auto auto auto",
  gap: 10,
  alignItems: "center",
  cursor: "pointer",
  transition: "background var(--dur-fast) var(--ease)",
};

function formatElapsed(start: number, end: number): string {
  const secs = Math.max(0, Math.round(end - start));
  if (secs < 60) return `${secs}s`;
  if (secs < 3600) return `${Math.round(secs / 60)}m`;
  return `${Math.round(secs / 3600)}h`;
}

function elapsedFor(j: RecentJobRow, now: number): string {
  const isRunning = j.state === "running";
  // Use started_ts (new field) when available; fall back to created_ts so
  // old rows loaded before a gateway restart still tick immediately.
  const start = j.started_ts ?? j.created_ts;
  return formatElapsed(start, isRunning ? now : j.updated_ts);
}

// Fan-out grouping. Two shapes:
//   * "single" — a stand-alone regular job. Rendered as one flat row.
//   * "group"  — a parent + its shards. Rendered collapsed by default;
//                click the caret to reveal the shard rows underneath.
type SingleEntry = { kind: "single"; job: RecentJobRow };
type GroupEntry = {
  kind: "group";
  parent: RecentJobRow;
  shards: RecentJobRow[];
  // ``sortTs`` drives interleaving with single rows; we key on the
  // parent's created_ts so a fan-out sits in the timeline where it was
  // dispatched (matches the raw list's ordering intuition).
  sortTs: number;
};
type Entry = SingleEntry | GroupEntry;

const IN_FLIGHT = new Set(["running", "assigned", "pending"]);

// Same aggregation rule as ``aggregateJobsToRuntime`` (canvas node
// status). Kept local because the shape here is RecentJobRow[] rather
// than SnapshotJob[]; extracting a shared helper would force both files
// to depend on a union type they otherwise don't need. If a third
// caller shows up, promote to a shared utility.
function aggregateGroupState(parent: RecentJobRow, shards: RecentJobRow[]): string {
  const all = [parent, ...shards];
  let hasFailed = false;
  let hasInFlight = false;
  let firstNonDone: string | null = null;
  let allDone = true;
  for (const j of all) {
    if (j.state !== "done") allDone = false;
    if (j.state === "failed") hasFailed = true;
    else if (IN_FLIGHT.has(j.state)) hasInFlight = true;
    if (j.state !== "done" && firstNonDone === null) firstNonDone = j.state;
  }
  if (hasFailed) return "failed";
  if (hasInFlight) return "running";
  if (allDone) return "done";
  return firstNonDone ?? "done";
}

// Total wall-clock time of a fan-out: earliest start → last shard's
// finish (or ``now`` if any shard is still running). Parent's
// ``started_ts`` is usually the earliest but we take the min for
// robustness against out-of-order shard startups.
function groupElapsed(
  parent: RecentJobRow,
  shards: RecentJobRow[],
  state: string,
  now: number,
): string {
  const all = [parent, ...shards];
  let earliest = Infinity;
  for (const j of all) {
    const s = j.started_ts ?? j.created_ts;
    if (s < earliest) earliest = s;
  }
  if (!isFinite(earliest)) return "";
  const running = state === "running";
  if (running) return formatElapsed(earliest, now);
  // Terminal — latest ``updated_ts`` across the whole group is when the
  // last shard finished.
  let latest = 0;
  for (const j of all) if (j.updated_ts > latest) latest = j.updated_ts;
  return formatElapsed(earliest, latest);
}

function buildEntries(rows: RecentJobRow[]): Entry[] {
  // Bucket shards by parent_job_id; parents (and regular jobs) go into
  // a lookup map so we can pair them up in one pass.
  const parents = new Map<string, RecentJobRow>();
  const shardsByParent = new Map<string, RecentJobRow[]>();
  const regulars: RecentJobRow[] = [];
  for (const j of rows) {
    if (j.parent_job_id) {
      const b = shardsByParent.get(j.parent_job_id);
      if (b) b.push(j);
      else shardsByParent.set(j.parent_job_id, [j]);
    } else {
      parents.set(j.job_id, j);
    }
  }
  // Any row with shards attached is a group; anything else is single.
  // A parentless orphan shard (shouldn't happen in practice) falls
  // through as a single row so we don't drop it silently.
  const entries: Entry[] = [];
  const groupedParentIds = new Set<string>();
  for (const [parentId, shards] of shardsByParent) {
    const parent = parents.get(parentId);
    if (!parent) {
      // Orphan shards — render each as a single row rather than losing them.
      for (const s of shards) entries.push({ kind: "single", job: s });
      continue;
    }
    groupedParentIds.add(parentId);
    // Sort shards by shard_element_id when set (stable, human-friendly);
    // fall back to created_ts.
    shards.sort((a, b) => {
      if (a.shard_element_id && b.shard_element_id) {
        return a.shard_element_id.localeCompare(b.shard_element_id);
      }
      return a.created_ts - b.created_ts;
    });
    entries.push({
      kind: "group",
      parent,
      shards,
      sortTs: parent.created_ts,
    });
  }
  for (const p of parents.values()) {
    if (groupedParentIds.has(p.job_id)) continue;
    regulars.push(p);
  }
  for (const r of regulars) entries.push({ kind: "single", job: r });
  // Newest first, matching the raw list order the panel already uses.
  entries.sort((a, b) => {
    const at = a.kind === "single" ? a.job.created_ts : a.sortTs;
    const bt = b.kind === "single" ? b.job.created_ts : b.sortTs;
    return bt - at;
  });
  return entries;
}

const SHARDS_INITIAL_LIMIT = 5;

export function RecentJobsPanel({
  jobs,
  onSelectGraphNode,
  currentWorkflowId,
}: RecentJobsPanelProps) {
  // Scope to the currently-loaded workflow when one is selected; otherwise
  // just show the newest handful across everything.
  const scoped = currentWorkflowId
    ? jobs.filter((j) => j.job_id && jobIsInWorkflow(j, currentWorkflowId))
    : jobs;
  // Cap by ENTRY count after grouping (25 entries), not by raw row count —
  // otherwise a fan-out with 21 shards would eat the whole panel budget
  // even though it collapses to a single row.
  const entries = buildEntries(scoped).slice(0, 25);

  // Live clock — ticks every second while anything on-screen is running
  // (single rows OR any job inside a group). Interval tears down when
  // everything visible is terminal, so idle panels incur zero overhead.
  const [now, setNow] = useState(() => Date.now() / 1000);
  const hasRunning = entries.some((e) => {
    if (e.kind === "single") return e.job.state === "running";
    return (
      e.parent.state === "running" ||
      e.shards.some((s) => s.state === "running")
    );
  });
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => {
    if (!hasRunning) {
      if (intervalRef.current !== null) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
      return;
    }
    intervalRef.current = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => {
      if (intervalRef.current !== null) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [hasRunning]);

  // Which group rows are currently expanded (keyed by parent job_id) and
  // whether the user opted to see "all N shards" beyond the initial cap.
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(
    () => new Set(),
  );
  const [expandedShardLists, setExpandedShardLists] = useState<Set<string>>(
    () => new Set(),
  );

  // Which job's log viewer is open. We keep the primer (algo name, state,
  // elapsed) alongside so the modal header renders before the /api/jobs/
  // roundtrip fills in fail_message.
  const [openLog, setOpenLog] = useState<{
    jobId: string;
    primer: {
      algorithm_name: string;
      algorithm_version: string;
      state: string;
      fail_reason: string | null;
      elapsed: string;
    };
  } | null>(null);

  return (
    <div
      style={{
        overflow: "auto",
        height: "100%",
        fontSize: "var(--fs-sm)",
        color: "var(--text-body)",
      }}
    >
      <div
        style={{
          padding: "10px 14px",
          fontWeight: 600,
          fontSize: 10,
          textTransform: "uppercase",
          color: "var(--text-subtle)",
          borderBottom: "1px solid var(--border)",
          background: "var(--surface-2)",
          letterSpacing: "0.08em",
          position: "sticky",
          top: 0,
          zIndex: 1,
        }}
      >
        Recent jobs
        {currentWorkflowId && (
          <span
            style={{
              marginLeft: 8,
              fontWeight: 400,
              textTransform: "none",
              letterSpacing: 0,
              color: "var(--text-muted)",
            }}
          >
            · current workflow
          </span>
        )}
      </div>
      {entries.length === 0 && (
        <div
          style={{ padding: 16, color: "var(--text-subtle)", fontSize: "var(--fs-sm)" }}
        >
          No jobs yet.
        </div>
      )}
      {entries.map((entry) => {
        if (entry.kind === "single") {
          return (
            <SingleJobRow
              key={entry.job.job_id}
              job={entry.job}
              now={now}
              onSelectGraphNode={onSelectGraphNode}
              onOpenLog={setOpenLog}
            />
          );
        }
        return (
          <GroupJobRow
            key={entry.parent.job_id}
            parent={entry.parent}
            shards={entry.shards}
            now={now}
            expanded={expandedGroups.has(entry.parent.job_id)}
            showAllShards={expandedShardLists.has(entry.parent.job_id)}
            onToggleExpand={() =>
              setExpandedGroups((prev) => {
                const next = new Set(prev);
                if (next.has(entry.parent.job_id)) next.delete(entry.parent.job_id);
                else next.add(entry.parent.job_id);
                return next;
              })
            }
            onShowAllShards={() =>
              setExpandedShardLists((prev) => {
                const next = new Set(prev);
                next.add(entry.parent.job_id);
                return next;
              })
            }
            onSelectGraphNode={onSelectGraphNode}
            onOpenLog={setOpenLog}
          />
        );
      })}
      <JobLogModal
        jobId={openLog?.jobId ?? null}
        primer={openLog?.primer ?? null}
        onClose={() => setOpenLog(null)}
      />
    </div>
  );
}

// Compact icon button — opens the JobLogModal for one row. Sized to
// match CopyRefButton (var(--control-h-sm)) so the two sit as a pair
// at the row's tail. The icon is a simple three-line "document" glyph
// (SVG so it doesn't render as an emoji on any platform); no colour on
// idle, gentle brightening on hover so it doesn't fight the copy button.
function ViewLogButton({
  onClick,
  state,
}: {
  onClick: (e: React.MouseEvent) => void;
  state: string;
}) {
  const isTerminal =
    state === "done" ||
    state === "failed" ||
    state === "cancelled" ||
    state === "orphaned";
  return (
    <button
      type="button"
      onClick={onClick}
      title={
        isTerminal
          ? state === "failed"
            ? "view failure log"
            : "view log"
          : "view log (running)"
      }
      aria-label="View job log"
      data-hl-view-log=""
      style={{
        width: "var(--control-h-sm)",
        height: "var(--control-h-sm)",
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-sm)",
        background: "var(--surface)",
        color: "var(--text-muted)",
        cursor: "pointer",
        padding: 0,
        transition:
          "background var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease)",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.background = "var(--surface-hover)";
        e.currentTarget.style.color = "var(--text)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = "var(--surface)";
        e.currentTarget.style.color = "var(--text-muted)";
      }}
    >
      <svg
        width="12"
        height="12"
        viewBox="0 0 16 16"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        aria-hidden="true"
      >
        <path d="M3 3.5h10M3 8h10M3 12.5h6" />
      </svg>
    </button>
  );
}

// State + primer shape shared between the panel and row components.
type OpenLogState = {
  jobId: string;
  primer: {
    algorithm_name: string;
    algorithm_version: string;
    state: string;
    fail_reason: string | null;
    elapsed: string;
  };
} | null;
type SetOpenLog = React.Dispatch<React.SetStateAction<OpenLogState>>;

// One flat row — used for both stand-alone jobs and, indented, for a
// group's expanded shard rows. ``indent`` shifts the leading dot column
// so shard rows read as visually nested under their parent.
function SingleJobRow({
  job,
  now,
  onSelectGraphNode,
  onOpenLog,
  label,
  indent,
}: {
  job: RecentJobRow;
  now: number;
  onSelectGraphNode: (graphNodeId: string) => void;
  onOpenLog: SetOpenLog;
  label?: string;
  indent?: boolean;
}) {
  const style: React.CSSProperties = indent
    ? { ...CARD, padding: "5px 14px 5px 34px", background: "var(--surface-2)" }
    : CARD;
  return (
    <div
      style={style}
      onClick={() => job.graph_node_id && onSelectGraphNode(job.graph_node_id)}
      title={job.job_id}
      onMouseEnter={(e) => {
        e.currentTarget.style.background = indent
          ? "var(--surface-hover)"
          : "var(--surface-hover)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = indent
          ? "var(--surface-2)"
          : "transparent";
      }}
    >
      <div
        style={{
          width: 8,
          height: 8,
          borderRadius: "var(--radius-pill)",
          background: stateColour(job.state),
        }}
      />
      <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        <strong style={{ color: "var(--text)", fontWeight: 600 }}>
          {label ?? job.algorithm_name}
        </strong>
        {!label && (
          <span
            style={{
              color: "var(--text-muted)",
              marginLeft: 5,
              fontFamily: "var(--font-mono)",
              fontSize: "var(--fs-xs)",
            }}
          >
            v{job.algorithm_version}
          </span>
        )}
      </div>
      <div
        style={{
          color: "var(--text-muted)",
          fontFamily: "var(--font-mono)",
          fontSize: "var(--fs-xs)",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {job.progress && job.progress.total > 0
          ? `${job.progress.current}/${job.progress.total}`
          : ""}
      </div>
      <div
        style={{
          color: "var(--text-subtle)",
          fontSize: "var(--fs-xs)",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {elapsedFor(job, now)}
      </div>
      <ViewLogButton
        onClick={(e) => {
          e.stopPropagation();
          onOpenLog({
            jobId: job.job_id,
            primer: {
              algorithm_name: job.algorithm_name,
              algorithm_version: job.algorithm_version,
              state: job.state,
              fail_reason: job.fail_reason ?? null,
              elapsed: elapsedFor(job, now),
            },
          });
        }}
        state={job.state}
      />
      <CopyRefButton
        kind="job"
        id={job.job_id}
        comment={`${job.algorithm_name} · ${job.state}`}
        size="xs"
      />
    </div>
  );
}

// Collapsed group summary: single row whose title is "``algo`` ×N" and
// whose elapsed cell shows the wall-clock time of the whole fan-out.
// Clicking the row toggles expansion; the caret is a passive indicator.
// The log button on the group row opens the *parent* job's log — the
// coordinator's stdout usually explains dispatch/fan-in behaviour.
function GroupJobRow({
  parent,
  shards,
  now,
  expanded,
  showAllShards,
  onToggleExpand,
  onShowAllShards,
  onSelectGraphNode,
  onOpenLog,
}: {
  parent: RecentJobRow;
  shards: RecentJobRow[];
  now: number;
  expanded: boolean;
  showAllShards: boolean;
  onToggleExpand: () => void;
  onShowAllShards: () => void;
  onSelectGraphNode: (graphNodeId: string) => void;
  onOpenLog: SetOpenLog;
}) {
  const state = aggregateGroupState(parent, shards);
  const elapsed = groupElapsed(parent, shards, state, now);
  const doneCount =
    (parent.state === "done" ? 1 : 0) +
    shards.filter((s) => s.state === "done").length;
  const total = shards.length + 1;
  const visibleShards = showAllShards
    ? shards
    : shards.slice(0, SHARDS_INITIAL_LIMIT);
  const hiddenCount = shards.length - visibleShards.length;

  return (
    <>
      <div
        style={CARD}
        onClick={onToggleExpand}
        title={parent.job_id}
        onMouseEnter={(e) => {
          e.currentTarget.style.background = "var(--surface-hover)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.background = "transparent";
        }}
      >
        <div
          style={{
            width: 8,
            height: 8,
            borderRadius: "var(--radius-pill)",
            background: stateColour(state),
          }}
        />
        <div
          style={{
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            display: "flex",
            alignItems: "center",
            gap: 6,
          }}
        >
          <span
            aria-hidden="true"
            style={{
              display: "inline-block",
              width: 10,
              color: "var(--text-muted)",
              fontSize: 10,
              transition: "transform var(--dur-fast) var(--ease)",
              transform: expanded ? "rotate(90deg)" : "rotate(0deg)",
            }}
          >
            ▸
          </span>
          <strong style={{ color: "var(--text)", fontWeight: 600 }}>
            {parent.algorithm_name}
          </strong>
          <span
            style={{
              color: "var(--text-muted)",
              fontFamily: "var(--font-mono)",
              fontSize: "var(--fs-xs)",
            }}
          >
            v{parent.algorithm_version}
          </span>
          <span
            style={{
              color: "var(--text-muted)",
              fontFamily: "var(--font-mono)",
              fontSize: "var(--fs-xs)",
              marginLeft: 2,
            }}
          >
            ×{total}
          </span>
        </div>
        <div
          style={{
            color: "var(--text-muted)",
            fontFamily: "var(--font-mono)",
            fontSize: "var(--fs-xs)",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {`${doneCount}/${total}`}
        </div>
        <div
          style={{
            color: "var(--text-subtle)",
            fontSize: "var(--fs-xs)",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {elapsed}
        </div>
        <ViewLogButton
          onClick={(e) => {
            e.stopPropagation();
            onOpenLog({
              jobId: parent.job_id,
              primer: {
                algorithm_name: parent.algorithm_name,
                algorithm_version: parent.algorithm_version,
                state,
                fail_reason: parent.fail_reason ?? null,
                elapsed,
              },
            });
          }}
          state={state}
        />
        <CopyRefButton
          kind="job"
          id={parent.job_id}
          comment={`${parent.algorithm_name} ×${total} · ${state}`}
          size="xs"
        />
      </div>
      {expanded && (
        <>
          {visibleShards.map((s) => (
            <SingleJobRow
              key={s.job_id}
              job={s}
              now={now}
              onSelectGraphNode={onSelectGraphNode}
              onOpenLog={onOpenLog}
              label={s.shard_element_id ?? s.job_id.slice(0, 8)}
              indent
            />
          ))}
          {hiddenCount > 0 && (
            <div
              onClick={onShowAllShards}
              style={{
                padding: "5px 14px 5px 34px",
                fontSize: "var(--fs-xs)",
                color: "var(--text-muted)",
                cursor: "pointer",
                background: "var(--surface-2)",
                borderBottom: "1px solid var(--border)",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.color = "var(--text)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.color = "var(--text-muted)";
              }}
            >
              显示全部 {shards.length} 个 shard
            </div>
          )}
        </>
      )}
    </>
  );
}

// Small helper to make the intent explicit — jobs table doesn't carry a
// workflow_id shortcut on RecentJobRow so we look it up upstream.
export function jobIsInWorkflow(job: RecentJobRow, wid: string): boolean {
  // The App builds RecentJobRow instances by copying only the fields it
  // needs; workflow membership is filtered at that seam. This function
  // stays as a hook for future extension (e.g. filter by snapshot).
  return Boolean(job) && Boolean(wid);
}

// Utility for the toolbar summary. Kept next to the RecentJobsPanel so the
// state → colour mapping stays local to this file's mental model.
export interface RunSummary {
  total: number;
  running: number;
  done: number;
  failed: number;
  pending: number;
}

export function summarise(jobs: RecentJobRow[]): RunSummary {
  const s: RunSummary = { total: 0, running: 0, done: 0, failed: 0, pending: 0 };
  for (const j of jobs) {
    s.total++;
    if (j.state === "running") s.running++;
    else if (j.state === "done") s.done++;
    else if (j.state === "failed" || j.state === "cancelled" || j.state === "orphaned")
      s.failed++;
    else s.pending++; // pending / assigned
  }
  return s;
}

export type NodeRuntimeMap = Record<string, NodeRuntime>;
