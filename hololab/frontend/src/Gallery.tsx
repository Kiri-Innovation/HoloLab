// Workflow Gallery — the landing page (route `/`).
//
// Figma-shaped: a card grid of every workflow, plus a "+ New workflow"
// affordance and a rename-in-place on each card. Clicking a card
// navigates to `/w/{id}` which the App-level router flips into the
// existing canvas view (see App.tsx).
//
// Deliberately no client-side router library — a popstate listener +
// pathname switch beats pulling in react-router for three screens.

import { useCallback, useEffect, useMemo, useState } from "react";
import type { WorkflowSummary } from "./api";
import { ApiError, deleteWorkflow, getWorkflow, listWorkflows, saveWorkflow } from "./api";
import { stateColour } from "./canvas/AlgorithmNode";
import { CopyRefButton } from "./canvas/CopyRefButton";
import { ThemeToggle } from "./theme/ThemeToggle";
import { CONTROL_STYLE, PRIMARY_CONTROL_STYLE } from "./ui/controlStyles";

const CARD_MIN_WIDTH = 260;

export interface GalleryProps {
  // Fired when the user picks a card or hits + New. The router (App)
  // maps this to a ``pushState('/w/…')`` so the browser back button works.
  onOpen: (workflowId: string) => void;
  // Fired when the user clicks the "Artifacts" link in the header. The
  // page-level nav is a peer of the workflow gallery — cleanup of past
  // runs' outputs is not a per-workflow concern.
  onOpenArtifacts: () => void;
}

export function Gallery({ onOpen, onOpenArtifacts }: GalleryProps) {
  const [rows, setRows] = useState<WorkflowSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState<boolean>(false);

  const refresh = useCallback(async () => {
    try {
      const r = await listWorkflows();
      setRows(r);
      setError(null);
    } catch (e) {
      const msg = e instanceof ApiError ? `HTTP ${e.status}` : (e as Error).message;
      setError(`could not load workflows: ${msg}`);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const onCreate = useCallback(async () => {
    if (creating) return;
    setCreating(true);
    try {
      const r = await saveWorkflow({
        name: defaultNewName(rows ?? []),
        graph: { nodes: [], edges: [] },
      });
      onOpen(r.workflow_id);
    } catch (e) {
      const msg = e instanceof ApiError ? `HTTP ${e.status}` : (e as Error).message;
      setError(`could not create workflow: ${msg}`);
      setCreating(false);
    }
  }, [creating, rows, onOpen]);

  const onRename = useCallback(
    async (row: WorkflowSummary, newName: string) => {
      const trimmed = newName.trim();
      if (!trimmed || trimmed === row.name) return;
      // We don't have the graph body here (list endpoint is lightweight);
      // fetch just what we need to preserve on rename.
      const detail = await getWorkflow(row.workflow_id);
      await saveWorkflow({
        workflow_id: row.workflow_id,
        name: trimmed,
        graph: detail.graph,
      });
      await refresh();
    },
    [refresh],
  );

  const onDelete = useCallback(
    async (row: WorkflowSummary) => {
      if (!window.confirm(`Delete "${row.name}"?\n\nThis removes the draft. Past runs stay in the database.`)) {
        return;
      }
      try {
        await deleteWorkflow(row.workflow_id);
        await refresh();
      } catch (e) {
        const msg = e instanceof ApiError ? `HTTP ${e.status}` : (e as Error).message;
        setError(`could not delete: ${msg}`);
      }
    },
    [refresh],
  );

  return (
    <div
      className="hl-app-shell"
      style={{
        height: "100vh",
        display: "grid",
        gridTemplateRows: "auto 1fr",
        background: "var(--bg)",
        color: "var(--text-body)",
        fontFamily: "var(--font-sans)",
      }}
    >
      <GalleryHeader
        creating={creating}
        onCreate={onCreate}
        onRefresh={refresh}
        onOpenArtifacts={onOpenArtifacts}
        workflowCount={rows?.length ?? 0}
      />
      <main className="hl-gallery-main" style={{ overflow: "auto", padding: "var(--space-7) var(--space-7) var(--space-8)" }}>
        {error && (
          <div
            style={{
              marginBottom: 12,
              padding: "8px 12px",
              background: "var(--error-soft)",
              color: "var(--error)",
              border: "1px solid var(--error)",
              borderRadius: "var(--radius-sm)",
              fontSize: "var(--fs-sm)",
            }}
          >
            {error}
          </div>
        )}
        <GalleryBody
          rows={rows}
          onOpen={onOpen}
          onRename={onRename}
          onDelete={onDelete}
          onCreate={onCreate}
        />
      </main>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------

function GalleryHeader({
  creating,
  onCreate,
  onRefresh,
  onOpenArtifacts,
  workflowCount,
}: {
  creating: boolean;
  onCreate: () => void;
  onRefresh: () => void;
  onOpenArtifacts: () => void;
  // Threaded into the ⧉ comment tail so a pasted ref reads
  // ``hololab://workflows  # 4 workflows`` — humans get the size hint
  // without having to open the page.
  workflowCount: number;
}) {
  return (
    <div
      className="hl-topbar"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "10px 24px",
        borderBottom: "1px solid var(--border)",
        background: "var(--surface)",
        fontSize: "var(--fs-sm)",
      }}
    >
      <div
        style={{
          fontWeight: 600,
          fontSize: "var(--fs-md)",
          color: "var(--text)",
          letterSpacing: "-0.01em",
        }}
      >
        HoloLab
      </div>
      <div style={{ color: "var(--text-muted)" }}>· workflows</div>
      {/*
        Index-kind ⧉: copies ``hololab://workflows  # N workflows`` so a
        user can point an agent at "this instance's workflow list"
        without naming a specific workflow. Sits right next to the
        page label so the semantic tie is visually obvious.
      */}
      <CopyRefButton
        kind="workflows"
        id=""
        comment={`${workflowCount} workflow${workflowCount === 1 ? "" : "s"}`}
        size="xs"
      />
      <button
        type="button"
        onClick={onOpenArtifacts}
        style={{
          ...CONTROL_STYLE,
          color: "var(--text-muted)",
          transition:
            "background var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease)",
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.background = "var(--surface-hover)";
          e.currentTarget.style.color = "var(--text)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.background = "transparent";
          e.currentTarget.style.color = "var(--text-muted)";
        }}
      >
        Artifacts
      </button>
      <div style={{ flex: 1 }} />
      <ThemeToggle />
      <button
        type="button"
        onClick={onRefresh}
        style={{
          ...CONTROL_STYLE,
        }}
      >
        Refresh
      </button>
      <button
        type="button"
        onClick={onCreate}
        disabled={creating}
        style={{
          ...PRIMARY_CONTROL_STYLE,
          cursor: creating ? "wait" : "pointer",
          opacity: creating ? 0.7 : 1,
        }}
      >
        + New workflow
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Body: loading / empty / grid
// ---------------------------------------------------------------------------

function GalleryBody({
  rows,
  onOpen,
  onRename,
  onDelete,
  onCreate,
}: {
  rows: WorkflowSummary[] | null;
  onOpen: (id: string) => void;
  onRename: (row: WorkflowSummary, newName: string) => Promise<void>;
  onDelete: (row: WorkflowSummary) => Promise<void>;
  onCreate: () => void;
}) {
  if (rows === null) {
    return (
      <div style={{ color: "var(--text-subtle)", padding: 40, fontSize: "var(--fs-sm)" }}>
        Loading…
      </div>
    );
  }
  if (rows.length === 0) {
    return <EmptyState onCreate={onCreate} />;
  }
  return (
    <div
      className="hl-gallery-grid"
      style={{
        display: "grid",
        gridTemplateColumns: `repeat(auto-fill, minmax(${CARD_MIN_WIDTH}px, 1fr))`,
        gap: 16,
      }}
    >
      {rows.map((r) => (
        <WorkflowCard
          key={r.workflow_id}
          row={r}
          onOpen={onOpen}
          onRename={onRename}
          onDelete={onDelete}
        />
      ))}
    </div>
  );
}

function EmptyState({ onCreate }: { onCreate: () => void }) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        color: "var(--text-muted)",
        padding: "80px 20px",
        gap: 14,
      }}
    >
      <div
        style={{
          fontSize: "var(--fs-xl)",
          fontWeight: 600,
          color: "var(--text)",
          letterSpacing: "-0.01em",
        }}
      >
        No workflows yet
      </div>
      <div
        style={{
          maxWidth: 460,
          textAlign: "center",
          fontSize: "var(--fs-sm)",
          lineHeight: 1.5,
        }}
      >
        Create your first workflow to start composing a pipeline. On the canvas,
        drag algorithm packs from the left palette onto the graph and connect
        them by their coloured ports.
      </div>
      <button
        type="button"
        onClick={onCreate}
        style={{
          marginTop: 8,
          border: "1px solid var(--accent)",
          background: "var(--accent)",
          color: "var(--accent-fg)",
          padding: "8px 18px",
          borderRadius: "var(--radius-sm)",
          cursor: "pointer",
          fontSize: "var(--fs-sm)",
          fontWeight: 600,
          height: "var(--control-h-lg)",
        }}
      >
        + Create your first workflow
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Card
// ---------------------------------------------------------------------------

function WorkflowCard({
  row,
  onOpen,
  onRename,
  onDelete,
}: {
  row: WorkflowSummary;
  onOpen: (id: string) => void;
  onRename: (row: WorkflowSummary, newName: string) => Promise<void>;
  onDelete: (row: WorkflowSummary) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(row.name);
  const [hover, setHover] = useState(false);

  const commit = async () => {
    setEditing(false);
    if (draft.trim() !== row.name) {
      try {
        await onRename(row, draft);
      } catch {
        setDraft(row.name); // roll back visually on failure
      }
    }
  };

  const relative = useMemo(() => formatRelative(row.updated_ts), [row.updated_ts]);

  return (
    <div className="hl-workflow-card"
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      onClick={() => !editing && onOpen(row.workflow_id)}
      style={{
        background: "var(--surface)",
        border: `1px solid ${hover ? "var(--border-strong)" : "var(--border)"}`,
        borderRadius: "var(--radius-md)",
        padding: 16,
        cursor: editing ? "text" : "pointer",
        display: "flex",
        flexDirection: "column",
        gap: 12,
        minHeight: 140,
        boxShadow: "none",
        transition:
          "border-color var(--dur-fast) var(--ease), background var(--dur-fast) var(--ease)",
        transform: "none",
      }}
    >
      <div style={{ display: "flex", alignItems: "flex-start", gap: 6 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          {editing ? (
            <input
              autoFocus
              value={draft}
              onClick={(e) => e.stopPropagation()}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={commit}
              onKeyDown={(e) => {
                if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                if (e.key === "Escape") {
                  setDraft(row.name);
                  setEditing(false);
                }
              }}
              style={{
                width: "100%",
                fontSize: "var(--fs-md)",
                fontWeight: 600,
                padding: "3px 6px",
                border: "1px solid var(--accent)",
                borderRadius: "var(--radius-sm)",
                background: "var(--surface)",
                color: "var(--text)",
                outline: "none",
              }}
            />
          ) : (
            <div
              onDoubleClick={(e) => {
                e.stopPropagation();
                setEditing(true);
              }}
              style={{
                fontSize: "var(--fs-md)",
                fontWeight: 600,
                color: "var(--text)",
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
                letterSpacing: "-0.005em",
              }}
              title={`${row.name} · double-click to rename`}
            >
              {row.name}
            </div>
          )}
          <div
            style={{
              fontSize: "var(--fs-xs)",
              color: "var(--text-subtle)",
              marginTop: 4,
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {row.node_count} node{row.node_count === 1 ? "" : "s"}
            <span style={{ margin: "0 6px" }}>·</span>
            updated {relative}
          </div>
        </div>
        {hover && !editing && (
          <>
            <IconButton
              title="rename"
              onClick={(e) => {
                e.stopPropagation();
                setEditing(true);
              }}
            >
              ✎
            </IconButton>
            <IconButton
              title="delete"
              onClick={(e) => {
                e.stopPropagation();
                void onDelete(row);
              }}
            >
              ×
            </IconButton>
          </>
        )}
      </div>

      <div style={{ flex: 1 }} />

      <LastRunLine last={row.last_run} />
    </div>
  );
}

function IconButton({
  children,
  title,
  onClick,
}: {
  children: React.ReactNode;
  title: string;
  onClick: (e: React.MouseEvent) => void;
}) {
  return (
    <button
      className="hl-control-sm"
      type="button"
      title={title}
      onClick={onClick}
      style={{
        border: "none",
        background: "transparent",
        padding: "2px 6px",
        cursor: "pointer",
        color: "var(--text-muted)",
        fontSize: "var(--fs-md)",
        lineHeight: 1,
        borderRadius: "var(--radius-sm)",
        transition: "background var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease)",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.background = "var(--surface-hover)";
        e.currentTarget.style.color = "var(--text)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = "transparent";
        e.currentTarget.style.color = "var(--text-muted)";
      }}
    >
      {children}
    </button>
  );
}

function LastRunLine({ last }: { last: WorkflowSummary["last_run"] }) {
  if (!last) {
    return (
      <div
        style={{
          fontSize: "var(--fs-xs)",
          color: "var(--text-subtle)",
          display: "flex",
          alignItems: "center",
          gap: 6,
        }}
      >
        <span
          style={{
            display: "inline-block",
            width: 6,
            height: 6,
            borderRadius: "var(--radius-pill)",
            background: "var(--border-strong)",
          }}
        />
        never run
      </div>
    );
  }
  const order = ["done", "failed", "running", "pending", "assigned", "cancelled", "orphaned"];
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        fontSize: "var(--fs-xs)",
        color: "var(--text-muted)",
        fontVariantNumeric: "tabular-nums",
      }}
    >
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: "var(--radius-pill)",
          background: stateColour(last.state),
        }}
      />
      <span>last run {formatRelative(last.created_ts)}</span>
      <span style={{ flex: 1 }} />
      {order
        .filter((s) => (last.state_counts[s] ?? 0) > 0)
        .map((s) => (
          <span
            key={s}
            title={`${last.state_counts[s]} ${s}`}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 3,
              color: "var(--text-body)",
            }}
          >
            <span
              style={{
                width: 5,
                height: 5,
                borderRadius: "var(--radius-pill)",
                background: stateColour(s),
              }}
            />
            {last.state_counts[s]}
          </span>
        ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

function defaultNewName(existing: WorkflowSummary[]): string {
  // Pick a stable-ish default so a spam-clicker doesn't get four rows
  // named "untitled". Untitled 1, Untitled 2 …
  const base = "Untitled";
  const taken = new Set(existing.map((r) => r.name));
  if (!taken.has(base)) return base;
  for (let i = 1; i < 999; i++) {
    const cand = `${base} ${i}`;
    if (!taken.has(cand)) return cand;
  }
  return base;
}

function formatRelative(secs: number): string {
  const now = Date.now() / 1000;
  const dt = Math.max(0, now - secs);
  if (dt < 60) return "just now";
  if (dt < 3600) return `${Math.round(dt / 60)}m ago`;
  if (dt < 86400) return `${Math.round(dt / 3600)}h ago`;
  if (dt < 86400 * 30) return `${Math.round(dt / 86400)}d ago`;
  const d = new Date(secs * 1000);
  return d.toISOString().slice(0, 10);
}
