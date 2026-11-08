import { RecipeFileActions } from "./canvas/RecipeFileActions";
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
import { ApiError, deleteWorkflow, getWorkflow, listWorkflows, moveWorkflowFile } from "./api";
import { WorkflowFilePicker } from "./canvas/WorkflowFiles";
import { PathInput } from "./canvas/PathInput";
import { editWorkflowFile } from "./canvas/editWorkflowFile";
import { stateColour } from "./canvas/AlgorithmNode";
import { CopyRefButton } from "./canvas/CopyRefButton";
import { ThemeToggle } from "./theme/ThemeToggle";
import { CONTROL_STYLE, PRIMARY_CONTROL_STYLE } from "./ui/controlStyles";
import { Modal } from "./ui/Modal";

const CARD_MIN_WIDTH = 260;

export interface GalleryProps {
  // Fired when the user picks a card or hits + New. The router (App)
  // maps this to a ``pushState('/w/…')`` so the browser back button works.
  onOpen: (workflowId: string) => void;
  // Fired when the user clicks the "Artifacts" link in the header. The
  // page-level nav is a peer of the workflow gallery — cleanup of past
  // runs' outputs is not a per-workflow concern.
  onOpenArtifacts: () => void;
  onOpenLibrary: () => void;
  onOpenPacks: () => void;
}

export function Gallery({ onOpen, onOpenArtifacts, onOpenLibrary, onOpenPacks }: GalleryProps) {
  const [rows, setRows] = useState<WorkflowSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [picker, setPicker] = useState<"create" | "open" | null>(null);
  const [moving, setMoving] = useState<WorkflowSummary | null>(null);

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

  // Gallery cards are summaries, so reload them when a canvas or another
  // Gallery tab changes a file-backed workflow (including a path-only move).
  useEffect(() => {
    const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/frontend`;
    let stopped = false;
    let retry: number | undefined;
    const connect = () => {
      const ws = new WebSocket(url);
      ws.onmessage = (event) => {
        try {
          const frame = JSON.parse(event.data);
          if (frame.kind === "workflow_changed" || frame.kind === "workflow_updated") void refresh();
        } catch {
          // Ignore unrelated malformed telemetry frames; the App connection
          // remains responsible for the canvas-level protocol.
        }
      };
      ws.onclose = () => {
        if (!stopped) retry = window.setTimeout(connect, 1000);
      };
      ws.onerror = () => ws.close();
    };
    connect();
    return () => {
      stopped = true;
      if (retry !== undefined) window.clearTimeout(retry);
    };
  }, [refresh]);

  const onCreate = useCallback(() => setPicker("create"), []);
  const onOpenFile = useCallback(() => setPicker("open"), []);

  const onRename = useCallback(
    async (row: WorkflowSummary, newName: string) => {
      const trimmed = newName.trim();
      if (!trimmed || trimmed === row.name) return;
      // We don't have the graph body here (list endpoint is lightweight);
      // fetch just what we need to preserve on rename.
      const detail = await getWorkflow(row.workflow_id);
      await editWorkflowFile(row.workflow_id,detail.rev,[{op:'name.set',value:trimmed}]);
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
        onCreate={onCreate}
        onOpenFile={onOpenFile}
        onOpen={onOpen}
        onRefresh={refresh}
        onOpenArtifacts={onOpenArtifacts}
        onOpenLibrary={onOpenLibrary}
        onOpenPacks={onOpenPacks}
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
          onMove={setMoving}
          onCreate={onCreate}
        />
      </main>
      {picker && (
        <WorkflowFilePicker
          mode={picker}
          onOpen={onOpen}
          onClose={() => setPicker(null)}
        />
      )}
      <WorkflowMoveDialog
        row={moving}
        onClose={() => setMoving(null)}
        onMoved={async () => {
          setMoving(null);
          await refresh();
        }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------

function GalleryHeader({
  onCreate,
  onOpenFile,
  onOpen,
  onRefresh,
  onOpenArtifacts,
  onOpenLibrary,
  onOpenPacks,
  workflowCount,
}: {
  onCreate: () => void;
  onOpenFile: () => void;
  onOpen: (workflowId: string) => void;
  onRefresh: () => void;
  onOpenArtifacts: () => void;
  onOpenLibrary: () => void;
  onOpenPacks: () => void;
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
        <img src="/hololab-icon.svg" width="20" height="20" alt="" aria-hidden="true" style={{ verticalAlign: "middle", marginRight: 6, flexShrink: 0 }} />
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
      <button type="button" onClick={onOpenLibrary} style={CONTROL_STYLE}>Library</button>
      <button type="button" onClick={onOpenPacks} style={CONTROL_STYLE}>Pack Manager</button>
      <RecipeFileActions onOpen={onOpen} compact />
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
      <button data-tooltip="刷新" aria-label="刷新"
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
        onClick={onOpenFile}
        style={CONTROL_STYLE}
      >
        打开 workflow
      </button>
      <button
        type="button"
        onClick={onCreate}
        style={PRIMARY_CONTROL_STYLE}
      >
        + 新建 workflow
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
  onMove,
  onCreate,
}: {
  rows: WorkflowSummary[] | null;
  onOpen: (id: string) => void;
  onRename: (row: WorkflowSummary, newName: string) => Promise<void>;
  onDelete: (row: WorkflowSummary) => Promise<void>;
  onMove: (row: WorkflowSummary) => void;
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
          onMove={onMove}
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
          ...PRIMARY_CONTROL_STYLE,
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
  onMove,
}: {
  row: WorkflowSummary;
  onOpen: (id: string) => void;
  onRename: (row: WorkflowSummary, newName: string) => Promise<void>;
  onDelete: (row: WorkflowSummary) => Promise<void>;
  onMove: (row: WorkflowSummary) => void;
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
              title="重命名"
              onClick={(e) => {
                e.stopPropagation();
                setEditing(true);
              }}
            >
              ✎
            </IconButton>
            <IconButton
              title="移动文件"
              onClick={(e) => {
                e.stopPropagation();
                onMove(row);
              }}
            >
              ↗
            </IconButton>
            <IconButton
              title="删除流程"
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

      {row.path && (
        <div
          className="hl-workflow-path"
          data-tooltip={row.path}
          data-tooltip-placement="below"
          tabIndex={0}
          aria-label={`服务器文件路径：${row.path}`}
        >
          {row.path}
        </div>
      )}

      <div style={{ flex: 1 }} />

      <LastRunLine last={row.last_run} />
    </div>
  );
}

function WorkflowMoveDialog({
  row,
  onClose,
  onMoved,
}: {
  row: WorkflowSummary | null;
  onClose: () => void;
  onMoved: () => Promise<void>;
}) {
  const [parentDir, setParentDir] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!row?.path) return;
    const slash = row.path.lastIndexOf("/");
    setParentDir(slash > 0 ? row.path.slice(0, slash) : "/");
    setName(row.path.slice(slash + 1));
    setError("");
  }, [row]);

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!row || busy) return;
    setBusy(true);
    setError("");
    try {
      await moveWorkflowFile(row.workflow_id, { parent_dir: parentDir, name });
      await onMoved();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={row !== null} onClose={busy ? () => {} : onClose} title="移动 workflow 文件" maxWidth={620}>
      <form onSubmit={submit} style={{ display: "grid", gap: "var(--space-4)" }}>
        <p style={{ margin: 0, color: "var(--text-muted)", fontSize: "var(--fs-sm)" }}>
          通过 HoloLab 移动会同时更新工作流登记路径。
        </p>
        <label style={FIELD_STYLE}>
          目标目录
          <PathInput nodeId="gateway" name="workflow-move-directory" value={parentDir} onChange={setParentDir} pathKind="directory" />
        </label>
        <label style={FIELD_STYLE}>
          文件名
          <input aria-label="目标 workflow 文件名" value={name} onChange={(e) => setName(e.target.value)} style={NAME_INPUT_STYLE} />
        </label>
        {error && <p role="alert" style={{ margin: 0, color: "var(--error)", fontSize: "var(--fs-sm)" }}>{error}</p>}
        <div style={ACTIONS_STYLE}>
          <button type="button" style={CONTROL_STYLE} onClick={onClose} disabled={busy}>取消</button>
          <button type="submit" style={PRIMARY_CONTROL_STYLE} disabled={busy || !parentDir || !name}>移动</button>
        </div>
      </form>
    </Modal>
  );
}

const FIELD_STYLE = {
  display: "grid",
  gap: "var(--space-2)",
  color: "var(--text-body)",
  fontSize: "var(--fs-sm)",
};

const NAME_INPUT_STYLE = {
  width: "100%",
  height: "var(--control-h-md)",
  boxSizing: "border-box" as const,
  padding: "0 var(--space-2)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-sm)",
  background: "var(--surface-2)",
  color: "var(--text-body)",
  fontSize: "var(--fs-sm)",
};

const ACTIONS_STYLE = {
  display: "flex",
  justifyContent: "flex-end",
  gap: "var(--space-2)",
  marginTop: "var(--space-2)",
};

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
      data-tooltip={title} aria-label={title}
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
