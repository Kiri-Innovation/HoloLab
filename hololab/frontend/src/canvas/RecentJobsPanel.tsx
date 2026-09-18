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
  const rows = scoped.slice(0, 25);

  // Live clock for running jobs — ticks every second so elapsed counters
  // advance without waiting for a WS event. The interval only runs when
  // at least one running row is visible; it's torn down when there are none
  // so idle panels incur zero overhead.
  const [now, setNow] = useState(() => Date.now() / 1000);
  const hasRunning = rows.some((j) => j.state === "running");
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
      {rows.length === 0 && (
        <div
          style={{ padding: 16, color: "var(--text-subtle)", fontSize: "var(--fs-sm)" }}
        >
          No jobs yet.
        </div>
      )}
      {rows.map((j) => (
        <div
          key={j.job_id}
          style={CARD}
          onClick={() => j.graph_node_id && onSelectGraphNode(j.graph_node_id)}
          title={j.job_id}
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
              background: stateColour(j.state),
            }}
          />
          <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            <strong style={{ color: "var(--text)", fontWeight: 600 }}>
              {j.algorithm_name}
            </strong>
            <span
              style={{
                color: "var(--text-muted)",
                marginLeft: 5,
                fontFamily: "var(--font-mono)",
                fontSize: "var(--fs-xs)",
              }}
            >
              v{j.algorithm_version}
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
            {j.progress && j.progress.total > 0
              ? `${j.progress.current}/${j.progress.total}`
              : ""}
          </div>
          <div
            style={{
              color: "var(--text-subtle)",
              fontSize: "var(--fs-xs)",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {elapsedFor(j, now)}
          </div>
          <ViewLogButton
            onClick={(e) => {
              e.stopPropagation();
              setOpenLog({
                jobId: j.job_id,
                primer: {
                  algorithm_name: j.algorithm_name,
                  algorithm_version: j.algorithm_version,
                  state: j.state,
                  fail_reason: j.fail_reason ?? null,
                  elapsed: elapsedFor(j, now),
                },
              });
            }}
            state={j.state}
          />
          <CopyRefButton
            kind="job"
            id={j.job_id}
            comment={`${j.algorithm_name} · ${j.state}`}
            size="xs"
          />
        </div>
      ))}
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
