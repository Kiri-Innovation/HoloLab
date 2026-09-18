// Run history — bottom section of the right sidebar.
//
// One view: every past snapshot for the current workflow, newest first,
// with favorited runs pinned to the top (within their own time-sorted group).
// Clicking a row opens the run on the canvas in read-only mode.
//
// V12 additions: per-run star (favorite) and Markdown note.

import { createPortal } from "react-dom";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { marked } from "marked";
import DOMPurify from "dompurify";
import type {
  RunSummaryRow,
  SnapshotDeletionPreview,
} from "../wire";
import {
  ApiError,
  deleteSnapshot,
  listWorkflowRuns,
  patchRun,
  previewSnapshotDeletion,
} from "../api";
import { stateColour } from "./AlgorithmNode";
import { CONTROL_STYLE } from "../ui/controlStyles";
import type { DiffItem } from "./diffGraphs";

// ---------------------------------------------------------------------------
// Markdown renderer — marked (v18) + DOMPurify for XSS safety.
// ---------------------------------------------------------------------------

function renderMd(source: string): string {
  const raw = marked(source, { async: false }) as string;
  return DOMPurify.sanitize(raw, { USE_PROFILES: { html: true } });
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface RunsPanelProps {
  workflowId: string;
  onOpenSnapshot: (snapshotId: string) => void;
  currentSnapshotId: string | null;
  draftDiff?: DiffItem[];
  onSnapshotDeleted?: (snapshotId: string) => void;
  refreshSignal?: number;
}

export function RunsPanel({
  workflowId,
  onOpenSnapshot,
  currentSnapshotId,
  draftDiff = [],
  onSnapshotDeleted,
  refreshSignal,
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

  useEffect(() => {
    if (!refreshSignal) return;
    void refresh();
  }, [refresh, refreshSignal]);

  // Optimistic toggle — update local state immediately, then PATCH.
  const handleToggleFavorite = useCallback(
    async (snapshotId: string, currentFav: boolean) => {
      const newVal = !currentFav;
      setRuns((prev) =>
        prev
          ? prev.map((r) =>
              r.snapshot_id === snapshotId ? { ...r, favorite: newVal } : r,
            )
          : prev,
      );
      try {
        await patchRun(workflowId, snapshotId, { favorite: newVal });
      } catch {
        // Revert on error.
        setRuns((prev) =>
          prev
            ? prev.map((r) =>
                r.snapshot_id === snapshotId ? { ...r, favorite: currentFav } : r,
              )
            : prev,
        );
      }
    },
    [workflowId],
  );

  const handleSaveNote = useCallback(
    async (snapshotId: string, note: string) => {
      const normalized = note.trim() || null;
      setRuns((prev) =>
        prev
          ? prev.map((r) =>
              r.snapshot_id === snapshotId ? { ...r, note: normalized } : r,
            )
          : prev,
      );
      await patchRun(workflowId, snapshotId, { note: normalized ?? "" });
    },
    [workflowId],
  );

  return (
    <div
      className="hl-runs-panel"
      style={{
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
        onDeleted={(sid) => {
          void refresh();
          onSnapshotDeleted?.(sid);
        }}
        onToggleFavorite={handleToggleFavorite}
        onSaveNote={handleSaveNote}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------

function Header({ onRefresh }: { onRefresh: () => void }) {
  return (
    <div
      className="hl-panel-header"
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
      <button type="button" onClick={onRefresh} title="refresh" style={{ ...CONTROL_STYLE }}>
        Refresh
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Run list
// ---------------------------------------------------------------------------

function RunListView({
  runs,
  currentSnapshotId,
  onOpen,
  draftDiff,
  onDeleted,
  onToggleFavorite,
  onSaveNote,
}: {
  runs: RunSummaryRow[] | null;
  currentSnapshotId: string | null;
  onOpen: (snapshotId: string) => void;
  draftDiff: DiffItem[];
  onDeleted: (snapshotId: string) => void;
  onToggleFavorite: (snapshotId: string, current: boolean) => void;
  onSaveNote: (snapshotId: string, note: string) => Promise<void>;
}) {
  const [diffModalOpen, setDiffModalOpen] = useState(false);
  const [menu, setMenu] = useState<{ snapshotId: string; x: number; y: number } | null>(null);
  const [confirm, setConfirm] = useState<{
    snapshotId: string;
    preview: SnapshotDeletionPreview | null;
    loading: boolean;
    deleting: boolean;
    error: string | null;
  } | null>(null);
  // Note editor state.
  const [noteEditor, setNoteEditor] = useState<{
    snapshotId: string;
    draft: string;
    saving: boolean;
    error: string | null;
  } | null>(null);
  // Per-row expanded note preview.
  const [expandedNotes, setExpandedNotes] = useState<Set<string>>(new Set());

  // Favorites pinned above non-favorites, each group sorted newest first.
  const sorted = useMemo(() => {
    if (!runs) return [];
    const favs = runs.filter((r) => r.favorite);
    const rest = runs.filter((r) => !r.favorite);
    return [...favs, ...rest];
  }, [runs]);

  const openConfirm = useCallback(async (snapshotId: string) => {
    setMenu(null);
    setConfirm({ snapshotId, preview: null, loading: true, deleting: false, error: null });
    try {
      const preview = await previewSnapshotDeletion(snapshotId);
      setConfirm((prev) =>
        prev?.snapshotId === snapshotId ? { ...prev, preview, loading: false } : prev,
      );
    } catch (e) {
      const msg = e instanceof ApiError ? `HTTP ${e.status}` : (e as Error).message;
      setConfirm((prev) =>
        prev?.snapshotId === snapshotId
          ? { ...prev, loading: false, error: `preview failed: ${msg}` }
          : prev,
      );
    }
  }, []);

  const runDelete = useCallback(async () => {
    if (!confirm) return;
    const sid = confirm.snapshotId;
    setConfirm({ ...confirm, deleting: true, error: null });
    try {
      await deleteSnapshot(sid);
      setConfirm(null);
      onDeleted(sid);
    } catch (e) {
      let msg = e instanceof ApiError ? `HTTP ${e.status}` : (e as Error).message;
      if (e instanceof ApiError && typeof e.detail === "object" && e.detail !== null) {
        const d = e.detail as { detail?: { message?: string } | string };
        if (typeof d.detail === "object" && d.detail?.message) msg = d.detail.message;
        else if (typeof d.detail === "string") msg = d.detail;
      }
      setConfirm((prev) => (prev ? { ...prev, deleting: false, error: msg } : prev));
    }
  }, [confirm, onDeleted]);

  const openNoteEditor = useCallback(
    (snapshotId: string, currentNote: string | null | undefined) => {
      setNoteEditor({ snapshotId, draft: currentNote ?? "", saving: false, error: null });
    },
    [],
  );

  const saveNote = useCallback(async () => {
    if (!noteEditor) return;
    setNoteEditor({ ...noteEditor, saving: true, error: null });
    try {
      await onSaveNote(noteEditor.snapshotId, noteEditor.draft);
      setNoteEditor(null);
    } catch (e) {
      const msg = e instanceof ApiError ? `HTTP ${e.status}` : (e as Error).message;
      setNoteEditor((prev) => (prev ? { ...prev, saving: false, error: msg } : prev));
    }
  }, [noteEditor, onSaveNote]);

  // Dismiss context menu on ESC / outside click.
  useEffect(() => {
    if (!menu) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setMenu(null); };
    const onClick = () => setMenu(null);
    window.addEventListener("keydown", onKey);
    window.addEventListener("click", onClick);
    window.addEventListener("contextmenu", onClick);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("click", onClick);
      window.removeEventListener("contextmenu", onClick);
    };
  }, [menu]);

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
            <span style={{ marginLeft: 5, color: "var(--text-muted)" }}>· 运行将创建新快照</span>
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
      {menu &&
        createPortal(
          <RunRowContextMenu
            x={menu.x}
            y={menu.y}
            onDelete={() => void openConfirm(menu.snapshotId)}
          />,
          document.body,
        )}
      {confirm &&
        createPortal(
          <DeleteConfirmModal
            snapshotId={confirm.snapshotId}
            preview={confirm.preview}
            loading={confirm.loading}
            deleting={confirm.deleting}
            error={confirm.error}
            onCancel={() => setConfirm(null)}
            onConfirm={() => void runDelete()}
          />,
          document.body,
        )}
      {noteEditor &&
        createPortal(
          <NoteEditorModal
            draft={noteEditor.draft}
            saving={noteEditor.saving}
            error={noteEditor.error}
            onChange={(v) => setNoteEditor((prev) => (prev ? { ...prev, draft: v } : prev))}
            onSave={() => void saveNote()}
            onCancel={() => setNoteEditor(null)}
          />,
          document.body,
        )}

      {sorted.map((r) => {
        const active = r.snapshot_id === currentSnapshotId;
        const favorited = r.favorite ?? false;
        const hasNote = !!(r.note && r.note.trim());
        const noteExpanded = expandedNotes.has(r.snapshot_id);
        return (
          <div
            key={r.snapshot_id}
            data-hl-run-row=""
            style={{
              borderBottom: "1px solid var(--border-subtle)",
              background: active ? "var(--accent-soft)" : "transparent",
              transition: "background var(--dur-fast) var(--ease)",
            }}
            onContextMenu={(e) => {
              e.preventDefault();
              e.stopPropagation();
              setMenu({ snapshotId: r.snapshot_id, x: e.clientX, y: e.clientY });
            }}
            onMouseEnter={(e) => {
              if (!active)
                (e.currentTarget as HTMLDivElement).style.background = "var(--surface-hover)";
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLDivElement).style.background = active
                ? "var(--accent-soft)"
                : "transparent";
            }}
          >
            {/* Main row — clicking opens the snapshot */}
            <div
              role="button"
              tabIndex={0}
              onClick={() => onOpen(r.snapshot_id)}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") onOpen(r.snapshot_id); }}
              title={active ? "currently open on canvas" : "open this run on the canvas"}
              style={{
                display: "grid",
                gridTemplateColumns: "8px 1fr auto",
                gap: 8,
                alignItems: "center",
                padding: "8px 12px 8px 12px",
                cursor: "pointer",
                fontSize: "var(--fs-sm)",
                color: "var(--text-body)",
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
                  {favorited && (
                    <span
                      title="已收藏"
                      style={{
                        padding: "1px 6px",
                        borderRadius: "var(--radius-pill)",
                        background: "var(--warning-soft, rgba(200, 162, 0, 0.12))",
                        color: "var(--warning, #c8a200)",
                        fontSize: 9,
                        fontWeight: 600,
                        letterSpacing: "0.03em",
                        textTransform: "uppercase",
                      }}
                    >
                      收藏
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
              {/* Right side: state pips + action icons (stop propagation) */}
              <div
                style={{ display: "flex", alignItems: "center", gap: 6 }}
                onClick={(e) => e.stopPropagation()}
                onKeyDown={(e) => e.stopPropagation()}
              >
                <StatePips counts={r.state_counts} />
                <IconButton
                  title={favorited ? "取消收藏" : "收藏此 run（置顶）"}
                  active={favorited}
                  onClick={() => onToggleFavorite(r.snapshot_id, favorited)}
                >
                  <StarIcon filled={favorited} />
                </IconButton>
                <IconButton
                  title={hasNote ? "查看 / 编辑备注" : "添加备注"}
                  active={hasNote}
                  onClick={() => openNoteEditor(r.snapshot_id, r.note)}
                >
                  <NoteIcon hasContent={hasNote} />
                </IconButton>
              </div>
            </div>

            {/* Inline note preview */}
            {hasNote && (
              <div
                style={{ padding: "0 12px 8px 26px" }}
                onClick={(e) => e.stopPropagation()}
              >
                <NotePreview
                  note={r.note!}
                  expanded={noteExpanded}
                  onToggle={() =>
                    setExpandedNotes((prev) => {
                      const next = new Set(prev);
                      if (next.has(r.snapshot_id)) next.delete(r.snapshot_id);
                      else next.add(r.snapshot_id);
                      return next;
                    })
                  }
                  onEdit={() => openNoteEditor(r.snapshot_id, r.note)}
                />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Icon button
// ---------------------------------------------------------------------------

function IconButton({
  title,
  active,
  onClick,
  children,
}: {
  title: string;
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        width: 22,
        height: 22,
        padding: 0,
        background: "none",
        border: "none",
        borderRadius: "var(--radius-sm, 4px)",
        cursor: "pointer",
        color: active ? "var(--accent, #4a9eff)" : "var(--text-subtle)",
        opacity: active ? 1 : 0.6,
        transition: "color var(--dur-fast) var(--ease), opacity var(--dur-fast) var(--ease)",
        flexShrink: 0,
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLButtonElement).style.opacity = "1";
        (e.currentTarget as HTMLButtonElement).style.background = "var(--surface-hover)";
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLButtonElement).style.opacity = active ? "1" : "0.6";
        (e.currentTarget as HTMLButtonElement).style.background = "none";
      }}
    >
      {children}
    </button>
  );
}

function StarIcon({ filled }: { filled: boolean }) {
  return filled ? (
    <svg width="13" height="13" viewBox="0 0 20 20" fill="currentColor" aria-hidden>
      <path d="M10 1.5l2.39 4.84 5.34.78-3.86 3.76.91 5.32L10 13.77l-4.78 2.51.91-5.32L2.27 7.12l5.34-.78z" />
    </svg>
  ) : (
    <svg
      width="13"
      height="13"
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M10 1.5l2.39 4.84 5.34.78-3.86 3.76.91 5.32L10 13.77l-4.78 2.51.91-5.32L2.27 7.12l5.34-.78z" />
    </svg>
  );
}

function NoteIcon({ hasContent }: { hasContent: boolean }) {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 20 20"
      fill={hasContent ? "currentColor" : "none"}
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <rect x="3" y="3" width="14" height="14" rx="2" />
      <line x1="6.5" y1="7" x2="13.5" y2="7" />
      <line x1="6.5" y1="10" x2="13.5" y2="10" />
      <line x1="6.5" y1="13" x2="10" y2="13" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Inline note preview
// ---------------------------------------------------------------------------

const NOTE_COLLAPSED_HEIGHT = 72;

function NotePreview({
  note,
  expanded,
  onToggle,
  onEdit,
}: {
  note: string;
  expanded: boolean;
  onToggle: () => void;
  onEdit: () => void;
}) {
  const html = useMemo(() => renderMd(note), [note]);
  const containerRef = useRef<HTMLDivElement>(null);
  const [overflows, setOverflows] = useState(false);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    setOverflows(el.scrollHeight > NOTE_COLLAPSED_HEIGHT + 4);
  }, [html]);

  return (
    <div
      style={{
        borderLeft: "2px solid var(--border)",
        paddingLeft: 8,
        position: "relative",
      }}
    >
      <div
        ref={containerRef}
        className="hl-md hl-note-preview"
        style={{
          maxHeight: expanded ? "none" : NOTE_COLLAPSED_HEIGHT,
          overflow: "hidden",
          fontSize: "var(--fs-xs)",
          color: "var(--text-muted)",
          lineHeight: 1.5,
        }}
        // eslint-disable-next-line react/no-danger
        dangerouslySetInnerHTML={{ __html: html }}
      />
      {!expanded && overflows && (
        <div
          style={{
            position: "absolute",
            bottom: 0,
            left: 0,
            right: 0,
            height: 24,
            background:
              "linear-gradient(transparent, var(--surface, #111))",
            pointerEvents: "none",
          }}
        />
      )}
      <div style={{ display: "flex", gap: 8, marginTop: 4, alignItems: "center" }}>
        {overflows && (
          <button
            type="button"
            onClick={onToggle}
            style={{
              fontSize: "var(--fs-xs)",
              background: "none",
              border: "none",
              padding: 0,
              color: "var(--text-subtle)",
              cursor: "pointer",
              textDecoration: "underline",
            }}
          >
            {expanded ? "收起" : "展开"}
          </button>
        )}
        <button
          type="button"
          onClick={onEdit}
          style={{
            fontSize: "var(--fs-xs)",
            background: "none",
            border: "none",
            padding: 0,
            color: "var(--accent, #4a9eff)",
            cursor: "pointer",
          }}
        >
          编辑备注
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Note editor modal
// ---------------------------------------------------------------------------

function NoteEditorModal({
  draft,
  saving,
  error,
  onChange,
  onSave,
  onCancel,
}: {
  draft: string;
  saving: boolean;
  error: string | null;
  onChange: (v: string) => void;
  onSave: () => void;
  onCancel: () => void;
}) {
  const [tab, setTab] = useState<"edit" | "preview">("edit");
  const html = useMemo(() => (draft.trim() ? renderMd(draft) : ""), [draft]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !saving) onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel, saving]);

  return (
    <div
      data-hl-note-editor-modal=""
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0, 0, 0, 0.72)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 10002,
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget && !saving) onCancel();
      }}
    >
      <div
        style={{
          background: "var(--bg-elevated, var(--surface-2, #1c1c1e))",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius-md, 8px)",
          width: "min(600px, 94vw)",
          maxHeight: "85vh",
          display: "flex",
          flexDirection: "column",
          boxShadow: "0 24px 64px rgba(0, 0, 0, 0.55)",
        }}
      >
        {/* Header */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            padding: "14px 20px 10px",
            borderBottom: "1px solid var(--border)",
            gap: 12,
          }}
        >
          <NoteIcon hasContent={!!draft.trim()} />
          <div style={{ flex: 1, fontWeight: 600, fontSize: "var(--fs-sm)", color: "var(--text)" }}>
            备注
          </div>
          {/* Tab switcher */}
          <div style={{ display: "flex", gap: 4 }}>
            {(["edit", "preview"] as const).map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => setTab(t)}
                style={{
                  padding: "3px 10px",
                  fontSize: "var(--fs-xs)",
                  background: tab === t ? "var(--accent-soft)" : "none",
                  border: "1px solid",
                  borderColor: tab === t ? "var(--accent, #4a9eff)" : "var(--border)",
                  borderRadius: "var(--radius-sm, 4px)",
                  color: tab === t ? "var(--accent, #4a9eff)" : "var(--text-muted)",
                  cursor: "pointer",
                }}
              >
                {t === "edit" ? "编辑" : "预览"}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={onCancel}
            disabled={saving}
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

        {/* Body */}
        <div style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
          {tab === "edit" ? (
            <textarea
              value={draft}
              onChange={(e) => onChange(e.target.value)}
              disabled={saving}
              placeholder="支持 Markdown 语法：# 标题  **加粗**  `code`  ``` 代码块 ```  [链接](url)"
              autoFocus
              style={{
                display: "block",
                width: "100%",
                minHeight: 220,
                padding: "14px 20px",
                fontFamily: "var(--font-mono)",
                fontSize: "var(--fs-sm)",
                color: "var(--text-body)",
                background: "transparent",
                border: "none",
                outline: "none",
                resize: "vertical",
                lineHeight: 1.6,
                boxSizing: "border-box",
              }}
            />
          ) : (
            <div
              style={{ padding: "14px 20px", minHeight: 80 }}
            >
              {html ? (
                <div
                  className="hl-md hl-note-rendered"
                  // eslint-disable-next-line react/no-danger
                  dangerouslySetInnerHTML={{ __html: html }}
                  style={{ fontSize: "var(--fs-sm)", color: "var(--text-body)", lineHeight: 1.6 }}
                />
              ) : (
                <div style={{ color: "var(--text-subtle)", fontSize: "var(--fs-sm)" }}>
                  （无内容）
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        {error && (
          <div
            style={{
              padding: "8px 20px",
              color: "var(--error)",
              fontSize: "var(--fs-xs)",
              background: "var(--error-soft)",
              borderTop: "1px solid var(--border)",
            }}
          >
            {error}
          </div>
        )}
        <div
          style={{
            display: "flex",
            justifyContent: "flex-end",
            gap: 8,
            padding: "12px 20px 14px",
            borderTop: "1px solid var(--border)",
          }}
        >
          <button
            type="button"
            onClick={onCancel}
            disabled={saving}
            style={{ ...CONTROL_STYLE, opacity: saving ? 0.55 : 1 }}
          >
            取消
          </button>
          <button
            type="button"
            onClick={onSave}
            disabled={saving}
            style={{
              ...CONTROL_STYLE,
              background: saving ? "var(--surface-alt)" : "var(--accent, #4a9eff)",
              color: saving ? "var(--text-subtle)" : "white",
              borderColor: saving ? "var(--border)" : "var(--accent, #4a9eff)",
              cursor: saving ? "not-allowed" : "pointer",
            }}
          >
            {saving ? "保存中…" : "保存"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers (unchanged from original)
// ---------------------------------------------------------------------------

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

function RunRowContextMenu({
  x,
  y,
  onDelete,
}: {
  x: number;
  y: number;
  onDelete: () => void;
}) {
  return (
    <div
      data-hl-run-menu=""
      role="menu"
      style={{
        position: "fixed",
        top: y,
        left: x,
        zIndex: 10000,
        minWidth: 220,
        background: "var(--bg-elevated, var(--surface-2, #1c1c1e))",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-md, 8px)",
        boxShadow: "0 12px 32px rgba(0, 0, 0, 0.45)",
        padding: 4,
      }}
      onClick={(e) => e.stopPropagation()}
      onContextMenu={(e) => e.preventDefault()}
    >
      <button
        type="button"
        data-hl-run-menu-delete=""
        onClick={onDelete}
        style={{
          display: "block",
          width: "100%",
          textAlign: "left",
          padding: "8px 12px",
          background: "none",
          border: "none",
          color: "var(--error, #e05a5a)",
          fontSize: "var(--fs-sm)",
          cursor: "pointer",
          borderRadius: "var(--radius-sm, 4px)",
        }}
        onMouseEnter={(e) => { e.currentTarget.style.background = "var(--surface-hover)"; }}
        onMouseLeave={(e) => { e.currentTarget.style.background = "none"; }}
      >
        删除这个 run 及其产物
      </button>
    </div>
  );
}

function DraftDiffModal({ items, onClose }: { items: DiffItem[]; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
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
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
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
        <div style={{ overflow: "auto", padding: "4px 0" }}>
          {items.length === 0 ? (
            <div
              style={{ padding: "14px 20px", color: "var(--text-muted)", fontSize: "var(--fs-sm)" }}
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
                  borderBottom: i < items.length - 1 ? "1px solid var(--border-subtle)" : "none",
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

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const kb = n / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  const mb = kb / 1024;
  if (mb < 1024) return `${mb.toFixed(1)} MB`;
  return `${(mb / 1024).toFixed(2)} GB`;
}

function DeleteConfirmModal({
  snapshotId,
  preview,
  loading,
  deleting,
  error,
  onCancel,
  onConfirm,
}: {
  snapshotId: string;
  preview: SnapshotDeletionPreview | null;
  loading: boolean;
  deleting: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !deleting) onCancel(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel, deleting]);

  const blocked = preview?.blocked ?? false;
  const canConfirm = !loading && !deleting && preview !== null && !blocked;

  return (
    <div
      data-hl-run-delete-modal=""
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0, 0, 0, 0.72)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 10001,
      }}
      onClick={(e) => { if (e.target === e.currentTarget && !deleting) onCancel(); }}
    >
      <div
        style={{
          background: "var(--bg-elevated, var(--surface-2, #1c1c1e))",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius-md, 8px)",
          width: "min(480px, 92vw)",
          display: "flex",
          flexDirection: "column",
          boxShadow: "0 24px 64px rgba(0, 0, 0, 0.55)",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: "16px 20px 12px",
            borderBottom: "1px solid var(--border)",
          }}
        >
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: 2,
              background: "var(--error, #e05a5a)",
              flexShrink: 0,
            }}
          />
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 700, fontSize: "var(--fs-sm)", color: "var(--text)" }}>
              删除这个 run 及其产物
            </div>
            <div
              style={{
                fontSize: "var(--fs-xs)",
                color: "var(--text-muted)",
                marginTop: 2,
                fontFamily: "var(--font-mono)",
              }}
            >
              {snapshotId.slice(0, 8)}
            </div>
          </div>
        </div>

        <div
          style={{
            padding: "16px 20px",
            fontSize: "var(--fs-sm)",
            color: "var(--text-body)",
            lineHeight: 1.55,
            minHeight: 100,
          }}
        >
          {loading && <div style={{ color: "var(--text-muted)" }}>计算影响范围…</div>}
          {!loading && preview && (
            <>
              {blocked ? (
                <div
                  data-hl-blocked=""
                  style={{
                    color: "var(--warning, #c8a200)",
                    background: "var(--warning-soft, rgba(200, 162, 0, 0.08))",
                    padding: "10px 12px",
                    borderRadius: "var(--radius-sm, 4px)",
                    border: "1px solid var(--border)",
                  }}
                >
                  <div style={{ fontWeight: 600, marginBottom: 4 }}>这个 run 里还有未结束的 job</div>
                  <div style={{ fontSize: "var(--fs-xs)", color: "var(--text-muted)" }}>
                    先取消这些 job，然后再删除。共{" "}
                    {preview.live_jobs.length} 个：
                    {preview.live_jobs.map((j) => `${j.algorithm_name}(${j.state})`).join("、")}
                  </div>
                </div>
              ) : (
                <>
                  <ImpactRow
                    label="将从磁盘删除"
                    value={String(preview.artifacts.exclusive_count)}
                    hint={preview.artifacts.exclusive_bytes > 0 ? `约 ${formatBytes(preview.artifacts.exclusive_bytes)}` : "0 B"}
                    accent="var(--error, #e05a5a)"
                  />
                  <ImpactRow
                    label="保留（其它 run 仍在引用）"
                    value={String(preview.artifacts.shared_count)}
                    hint={preview.artifacts.shared_count > 0 ? "只解除引用，物理文件保留" : "无"}
                    accent="var(--text-muted)"
                  />
                  <ImpactRow
                    label="Job 记录清除 / 保留"
                    value={`${preview.jobs.exclusive_count} / ${preview.jobs.shared_count}`}
                    hint="共享 job 属于其它 run，仅解除本 run 的关联"
                    accent="var(--text-muted)"
                  />
                </>
              )}
            </>
          )}
          {error && (
            <div
              style={{
                marginTop: 10,
                color: "var(--error)",
                fontSize: "var(--fs-xs)",
                background: "var(--error-soft)",
                padding: "8px 10px",
                borderRadius: "var(--radius-sm, 4px)",
              }}
            >
              {error}
            </div>
          )}
        </div>

        <div
          style={{
            display: "flex",
            justifyContent: "flex-end",
            gap: 8,
            padding: "12px 20px 16px",
            borderTop: "1px solid var(--border)",
          }}
        >
          <button
            type="button"
            data-hl-cancel=""
            onClick={onCancel}
            disabled={deleting}
            style={{ ...CONTROL_STYLE, opacity: deleting ? 0.55 : 1 }}
          >
            取消
          </button>
          <button
            type="button"
            data-hl-confirm-delete=""
            onClick={onConfirm}
            disabled={!canConfirm}
            style={{
              ...CONTROL_STYLE,
              background: canConfirm ? "var(--error, #e05a5a)" : "var(--surface-alt)",
              color: canConfirm ? "white" : "var(--text-subtle)",
              borderColor: canConfirm ? "var(--error, #e05a5a)" : "var(--border)",
              cursor: canConfirm ? "pointer" : "not-allowed",
            }}
          >
            {deleting ? "删除中…" : "确认删除"}
          </button>
        </div>
      </div>
    </div>
  );
}

function ImpactRow({
  label,
  value,
  hint,
  accent,
}: {
  label: string;
  value: string;
  hint: string;
  accent: string;
}) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "1fr auto",
        gap: 12,
        alignItems: "baseline",
        padding: "8px 0",
        borderBottom: "1px solid var(--border-subtle)",
      }}
    >
      <div>
        <div style={{ color: "var(--text)", fontSize: "var(--fs-sm)" }}>{label}</div>
        <div style={{ color: "var(--text-muted)", fontSize: "var(--fs-xs)", marginTop: 1 }}>
          {hint}
        </div>
      </div>
      <div
        style={{
          color: accent,
          fontVariantNumeric: "tabular-nums",
          fontWeight: 600,
          fontSize: "var(--fs-md, 15px)",
        }}
      >
        {value}
      </div>
    </div>
  );
}
