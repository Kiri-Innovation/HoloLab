// Compact recent-jobs list. Sits under the NodeInspector at the bottom of
// the layout. Clicking a row selects the corresponding canvas node so the
// user can jump from a runtime event to the node that produced it.

import type { NodeRuntime } from "./AlgorithmNode";
import { stateColour } from "./AlgorithmNode";
import { CopyRefButton } from "./CopyRefButton";

export interface RecentJobRow {
  job_id: string;
  algorithm_name: string;
  algorithm_version: string;
  state: string;
  graph_node_id: string | null;
  progress?: { current: number; total: number } | null;
  updated_ts: number;
  created_ts: number;
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
  gridTemplateColumns: "10px 1fr auto auto auto",
  gap: 10,
  alignItems: "center",
  cursor: "pointer",
  transition: "background var(--dur-fast) var(--ease)",
};

function formatElapsed(created: number, updated: number): string {
  const secs = Math.max(0, Math.round(updated - created));
  if (secs < 60) return `${secs}s`;
  if (secs < 3600) return `${Math.round(secs / 60)}m`;
  return `${Math.round(secs / 3600)}h`;
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
            {formatElapsed(j.created_ts, j.updated_ts)}
          </div>
          <CopyRefButton
            kind="job"
            id={j.job_id}
            comment={`${j.algorithm_name} · ${j.state}`}
            size="xs"
          />
        </div>
      ))}
    </div>
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
