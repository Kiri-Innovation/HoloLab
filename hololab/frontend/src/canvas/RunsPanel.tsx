// Run history — bottom section of the right sidebar.
//
// One view: every past snapshot for the current workflow, newest first.
// Clicking a row asks the App to swap the canvas into read-only snapshot
// mode (see AppInner.viewingSnapshot). Whichever snapshot is open on the
// canvas is highlighted here so the context is always visible.
//
// Used to be a floating overlay; the sidebar embed keeps it visible next
// to the compute node card so both live surfaces (nodes + runs) sit in
// one column instead of fighting the canvas for space.

import { createPortal } from "react-dom";
import { useCallback, useEffect, useState } from "react";
import type { RunSummaryRow } from "../wire";
import { ApiError, listWorkflowRuns } from "../api";
import { stateColour } from "./AlgorithmNode";
import { CONTROL_STYLE } from "../ui/controlStyles";
import type { DiffItem } from "./diffGraphs";

export interface RunsPanelProps {
  workflowId: string;
  // Fired when a row is clicked. The App fetches the snapshot detail and
  // enters read-only canvas mode. We don't fetch here so the panel stays
  // presentation-only.
  onOpenSnapshot: (snapshotId: string) => void;
  // Which snapshot (if any) is currently open on the canvas — the panel
  // highlights its row and shows a "当前" chip so the context is obvious.
  currentSnapshotId: string | null;
  // Structural diff between the in-memory draft and the latest snapshot.
  // Empty when no snapshot exists yet or when the draft matches. Non-empty
  // → sentinel row + "检查" button appear at the top of the list.
  draftDiff?: DiffItem[];
}

export function RunsPanel({
  workflowId,
  onOpenSnapshot,
  currentSnapshotId,
  draftDiff = [],
}: RunsPanelProps) {
  const [runs, setRuns] = useState<RunSummaryRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const rows = await listWorkflowRuns(workflowId);
      setRuns(rows);
      setError(null);
    } catch (e) {
      const msg = e instanceof ApiError ? `HTTP ${e.status}` : (e as Error).message;
      setError(`could not load runs: ${msg}`);
    }
  }, [workflowId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <div className="hl-runs-panel"
      style={{
        // Fill the parent flex/grid slot the sidebar hands us. minHeight:0
        // is what lets the inner scrollbar of ``RunListView`` actually
        // kick in when the list is longer than the column.
        flex: 1,
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
        fontSize: "var(--fs-sm)",
        color: "var(--text-body)",
        background: "var(--surface)",
      }}
    >
      <Header onRefresh={refresh} />
      {error && (
        <div
          style={{
            padding: 12,
            color: "var(--error)",
            fontSize: "var(--fs-xs)",
            background: "var(--error-soft)",
          }}
        >
          {error}
        </div>
      )}
      <RunListView
        runs={runs}
        currentSnapshotId={currentSnapshotId}
        onOpen={onOpenSnapshot}
        draftDiff={draftDiff}
      />
    </div>
  );
}

function Header({ onRefresh }: { onRefresh: () => void }) {
  return (
    <div className="hl-panel-header"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "12px 16px",
        borderBottom: "1px solid var(--border)",
        background: "var(--surface-2)",
      }}
    >
      <div style={{ flex: 1 }}>
        <div
          style={{
            fontSize: 10,
            fontWeight: 600,
            letterSpacing: "0.08em",
            textTransform: "uppercase",
            color: "var(--text-subtle)",
          }}
        >
          Run history
        </div>
      </div>
      <button
        type="button"
        onClick={onRefresh}
        title="refresh"
        style={{
          ...CONTROL_STYLE,
        }}
      >
        Refresh
      </button>
    </div>
  );
}

function RunListView({
  runs,
  currentSnapshotId,
  onOpen,
  draftDiff,
}: {
  runs: RunSummaryRow[] | null;
  currentSnapshotId: string | null;
  onOpen: (snapshotId: string) => void;
  draftDiff: DiffItem[];
}) {
  const [diffModalOpen, setDiffModalOpen] = useState(false);

  if (runs === null) {
    return (
      <div style={{ padding: 14, color: "var(--text-subtle)", fontSize: "var(--fs-sm)" }}>
        Loading…
      </div>
    );
  }
  if (runs.length === 0) {
    return (
      <div
        style={{
          padding: 14,
          color: "var(--text-muted)",
          fontSize: "var(--fs-sm)",
          lineHeight: 1.5,
        }}
      >
        No runs yet. Hit <strong style={{ color: "var(--text)" }}>Run</strong> in the toolbar
        to record one.
      </div>
    );
  }
  return (
    <div style={{ overflow: "auto", flex: 1 }}>
      {draftDiff.length > 0 && (
        <div
          data-hl-draft-modified-row=""
          title="The draft has structural changes (nodes / params / edges / assigned node) not yet captured in a snapshot."
          style={{
            padding: "8px 12px",
            borderBottom: "1px dashed var(--border)",
            background: "var(--warning-soft, rgba(200, 162, 0, 0.07))",
            display: "flex",
            alignItems: "center",
            gap: 8,
            fontSize: "var(--fs-sm)",
          }}
        >
          <span
            style={{
              width: 6,
              height: 6,
              borderRadius: 1,
              background: "var(--warning, #c8a200)",
              flexShrink: 0,
            }}
          />
          <div style={{ flex: 1 }}>
            <span style={{ fontWeight: 600, color: "var(--text)" }}>草稿有结构改动</span>
            <span style={{ marginLeft: 5, color: "var(--text-muted)" }}>· 待运行</span>
          </div>
          <button
            type="button"
            data-hl-check-diff=""
            onClick={() => setDiffModalOpen(true)}
            style={{
              padding: "2px 8px",
              fontSize: "var(--fs-xs)",
              background: "none",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius-sm, 4px)",
              cursor: "pointer",
              color: "var(--text-muted)",
              whiteSpace: "nowrap",
              flexShrink: 0,
            }}
          >
            检查
          </button>
        </div>
      )}
      {diffModalOpen &&
        createPortal(
          <DraftDiffModal items={draftDiff} onClose={() => setDiffModalOpen(false)} />,
          document.body,
        )}
      {runs.map((r) => {
        const active = r.snapshot_id === currentSnapshotId;
        return (
          <button
            className="hl-row-action"
            key={r.snapshot_id}
            type="button"
            onClick={() => onOpen(r.snapshot_id)}
            style={{
              width: "100%",
              display: "grid",
              gridTemplateColumns: "8px 1fr auto",
              gap: 8,
              alignItems: "center",
              padding: "8px 12px",
              border: "none",
              borderBottom: "1px solid var(--border-subtle)",
              background: active ? "var(--accent-soft)" : "transparent",
              cursor: "pointer",
              textAlign: "left",
              fontSize: "var(--fs-sm)",
              color: "var(--text-body)",
              transition: "background var(--dur-fast) var(--ease)",
            }}
            title={active ? "currently open on canvas" : "open this run on the canvas"}
            onMouseEnter={(e) => {
              if (!active)
                e.currentTarget.style.background = "var(--surface-hover)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = active
                ? "var(--accent-soft)"
                : "transparent";
            }}
          >
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: "var(--radius-pill)",
                background: stateColour(r.state),
              }}
            />
            <div style={{ minWidth: 0 }}>
              <div
                style={{
                  fontWeight: 600,
                  color: "var(--text)",
                  fontVariantNumeric: "tabular-nums",
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  flexWrap: "wrap",
                }}
              >
                {formatTs(r.created_ts)}
                {active && (
                  <span
                    data-hl-current-run=""
                    style={{
                      padding: "1px 6px",
                      borderRadius: "var(--radius-pill)",
                      background: "var(--accent-soft)",
                      color: "var(--accent, #4a9eff)",
                      fontSize: 9,
                      fontWeight: 600,
                      letterSpacing: "0.03em",
                      textTransform: "uppercase",
                    }}
                  >
                    当前
                  </span>
                )}
              </div>
              <div
                style={{
                  color: "var(--text-muted)",
                  fontSize: "var(--fs-xs)",
                  marginTop: 2,
                }}
              >
                {r.node_count} nodes · {r.job_count} jobs
                <span
                  style={{
                    marginLeft: 6,
                    fontFamily: "var(--font-mono)",
                    color: "var(--text-subtle)",
                  }}
                >
                  {r.snapshot_id.slice(0, 8)}
                </span>
                {(r.artifact_counts?.deleted ?? 0) > 0 && (
                  <span
                    title={
                      `${r.artifact_counts?.deleted} artifact(s) cleaned via the Artifacts page. ` +
                      "Previews for these will 404 — the run row stays so history is complete."
                    }
                    style={{
                      marginLeft: 6,
                      padding: "1px 6px",
                      borderRadius: "var(--radius-pill)",
                      background: "var(--surface-alt)",
                      color: "var(--text-subtle)",
                      fontSize: 9,
                      fontWeight: 600,
                      letterSpacing: "0.03em",
                      textTransform: "uppercase",
                    }}
                  >
                    {r.artifact_counts?.deleted} deleted
                  </span>
                )}
              </div>
            </div>
            <StatePips counts={r.state_counts} />
          </button>
        );
      })}
    </div>
  );
}

function DraftDiffModal({
  items,
  onClose,
}: {
  items: DiffItem[];
  onClose: () => void;
}) {
  // Close on Escape key.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      data-hl-draft-diff-modal=""
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0, 0, 0, 0.72)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 9999,
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        style={{
          background: "var(--bg-elevated, var(--surface-2, #1c1c1e))",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius-md, 8px)",
          width: "min(540px, 92vw)",
          maxHeight: "70vh",
          display: "flex",
          flexDirection: "column",
          boxShadow: "0 24px 64px rgba(0, 0, 0, 0.55)",
        }}
      >
        {/* header */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            padding: "16px 20px 12px",
            borderBottom: "1px solid var(--border)",
            gap: 12,
          }}
        >
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: 2,
              background: "var(--warning, #c8a200)",
              flexShrink: 0,
            }}
          />
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 700, fontSize: "var(--fs-sm)", color: "var(--text)" }}>
              草稿结构改动
            </div>
            <div style={{ fontSize: "var(--fs-xs)", color: "var(--text-muted)", marginTop: 2 }}>
              {items.length} 项改动 · 相较最新快照
            </div>
          </div>
          <button
            type="button"
            data-hl-draft-diff-close=""
            onClick={onClose}
            style={{
              background: "none",
              border: "none",
              cursor: "pointer",
              color: "var(--text-muted)",
              fontSize: 16,
              padding: "2px 6px",
              lineHeight: 1,
              borderRadius: "var(--radius-sm, 4px)",
            }}
          >
            ✕
          </button>
        </div>
        {/* diff list */}
        <div style={{ overflow: "auto", padding: "4px 0" }}>
          {items.length === 0 ? (
            <div
              style={{
                padding: "14px 20px",
                color: "var(--text-muted)",
                fontSize: "var(--fs-sm)",
              }}
            >
              无法计算差异详情。
            </div>
          ) : (
            items.map((item, i) => (
              <div
                key={i}
                data-hl-diff-item=""
                style={{
                  padding: "7px 20px",
                  fontSize: "var(--fs-xs)",
                  fontFamily: "var(--font-mono)",
                  color: "var(--text-body)",
                  borderBottom:
                    i < items.length - 1 ? "1px solid var(--border-subtle)" : "none",
                  lineHeight: 1.6,
                  wordBreak: "break-all",
                }}
              >
                {item.description}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

function StatePips({ counts }: { counts: Record<string, number> }) {
  const order = ["done", "failed", "running", "pending", "assigned", "cancelled", "orphaned"];
  return (
    <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
      {order
        .filter((s) => (counts[s] ?? 0) > 0)
        .map((s) => (
          <span
            key={s}
            title={`${counts[s]} ${s}`}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 3,
              fontSize: 10,
              color: "var(--text-body)",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: "var(--radius-pill)",
                background: stateColour(s),
              }}
            />
            {counts[s]}
          </span>
        ))}
    </div>
  );
}

function formatTs(secs: number): string {
  const d = new Date(secs * 1000);
  const iso = d.toISOString();
  return `${iso.slice(0, 10)} ${iso.slice(11, 19)}`;
}
