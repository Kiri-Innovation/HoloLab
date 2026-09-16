// Artifacts page — inventory + cleanup for run outputs.
//
// The Gallery counterpart: instead of workflows, this page lists the
// handles those workflows have produced, grouped by workflow with
// per-row cleanup and a "sweep dead handles" bulk action. Behaviour
// notes:
//
//   * The filesystem check is opt-in — the "Check liveness" button
//     re-hits ``/api/artifacts?check=1`` so the state chip flips from
//     ``pending`` to one of ``alive|incomplete|dead``. A page load
//     that skipped the check just shows ``pending`` chips + the
//     ``deleted`` bucket the DB already knows about.
//   * Delete actions always confirm. Bulk "Delete dead" only runs
//     once a liveness check has happened, so we never nuke a row we
//     haven't verified is really gone.
//   * We keep the DB row after deletion — the state chip becomes
//     ``deleted`` and the row stays visible so run history still
//     reads as a full account of what was produced.

import { useCallback, useEffect, useMemo, useState } from "react";
import type { ArtifactRow, ArtifactState, ArtifactListResponse } from "./api";
import {
  ApiError,
  bulkDeleteArtifacts,
  deleteArtifact,
  listArtifacts,
  listWorkflowRuns,
} from "./api";
import type { RunSummaryRow } from "./wire";
import { CopyRefButton } from "./canvas/CopyRefButton";
import { ThemeToggle } from "./theme/ThemeToggle";
import { CONTROL_STYLE } from "./ui/controlStyles";

const STATE_COLOURS: Record<ArtifactState, string> = {
  alive: "var(--success)",
  incomplete: "var(--warning)",
  dead: "var(--error)",
  deleted: "var(--text-subtle)",
  pending: "var(--info)",
};

const STATE_ORDER: ArtifactState[] = [
  "alive",
  "incomplete",
  "dead",
  "deleted",
  "pending",
];

export interface ArtifactsProps {
  onBackToGallery: () => void;
}

export function Artifacts({ onBackToGallery }: ArtifactsProps) {
  const [data, setData] = useState<ArtifactListResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [checked, setChecked] = useState(false);
  const [stateFilter, setStateFilter] = useState<ArtifactState | "all">("all");
  const [workflowFilter, setWorkflowFilter] = useState<string | "all">("all");
  // Chained run filter — populated from the runs list of the selected
  // workflow. Reset to "all" whenever the workflow changes so the two
  // dropdowns can't drift out of sync (a snapshot from workflow A would
  // filter every row to empty if workflow B were then picked).
  const [snapshotFilter, setSnapshotFilter] = useState<string | "all">("all");
  const [runs, setRuns] = useState<RunSummaryRow[]>([]);
  const [runsLoading, setRunsLoading] = useState(false);
  // Multi-select for the bulk actions bar. A ``Set`` keyed by handle_id
  // so toggling one row is O(1). Cleared whenever the underlying
  // filters or the fetched row set change — a row the user can no
  // longer see must never end up in a "Delete N" operation.
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [bulkBusy, setBulkBusy] = useState(false);

  const load = useCallback(
    async (opts?: { check?: boolean }) => {
      setLoading(true);
      setError(null);
      try {
        const r = await listArtifacts({
          check: opts?.check ?? checked,
          limit: 1000,
        });
        setData(r);
        if (opts?.check !== undefined) setChecked(opts.check);
      } catch (e) {
        const msg = e instanceof ApiError ? `HTTP ${e.status}` : (e as Error).message;
        setError(`could not load artifacts: ${msg}`);
      } finally {
        setLoading(false);
      }
    },
    [checked],
  );

  useEffect(() => {
    void load({ check: false });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Fetch the run list for the selected workflow. Runs are cheap
  // (metadata rows, no jobs) so we refetch on every workflow flip
  // rather than caching — keeps the dropdown fresh if the user
  // triggered a new run in another tab.
  useEffect(() => {
    // Reset the chained filter first: a stale snapshot_id would
    // filter every row to empty on the target workflow.
    setSnapshotFilter("all");
    if (workflowFilter === "all") {
      setRuns([]);
      return;
    }
    let cancelled = false;
    setRunsLoading(true);
    listWorkflowRuns(workflowFilter)
      .then((r) => {
        if (!cancelled) setRuns(r);
      })
      .catch(() => {
        // Non-fatal: fall back to an empty run list; the dropdown
        // renders "All runs" only and the workflow filter still works.
        if (!cancelled) setRuns([]);
      })
      .finally(() => {
        if (!cancelled) setRunsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [workflowFilter]);

  const rowsFiltered = useMemo(() => {
    if (!data) return [];
    return data.rows.filter((r) => {
      if (stateFilter !== "all" && r.state !== stateFilter) return false;
      if (workflowFilter !== "all" && r.workflow_id !== workflowFilter) return false;
      if (snapshotFilter !== "all" && r.snapshot_id !== snapshotFilter) return false;
      return true;
    });
  }, [data, stateFilter, workflowFilter, snapshotFilter]);

  // Clear selection whenever the visible row set can change out from
  // under us. The safe/simple rule: any filter flip or refetch drops
  // the selection. "Some selected rows might have survived" is not
  // worth the confusion of accidentally-carried-over selections when
  // the user pivoted to a different scope.
  useEffect(() => {
    if (selectedIds.size > 0) setSelectedIds(new Set());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, stateFilter, workflowFilter, snapshotFilter]);

  const selectedRows = useMemo(
    () => rowsFiltered.filter((r) => selectedIds.has(r.handle_id)),
    [rowsFiltered, selectedIds],
  );
  const selectedBytes = useMemo(
    () =>
      selectedRows.reduce(
        (a, r) => a + (r.live_size_bytes ?? r.size_bytes ?? 0),
        0,
      ),
    [selectedRows],
  );
  const allFilteredSelected =
    rowsFiltered.length > 0 && rowsFiltered.every((r) => selectedIds.has(r.handle_id));

  const toggleRow = useCallback((handle_id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(handle_id)) next.delete(handle_id);
      else next.add(handle_id);
      return next;
    });
  }, []);

  const toggleAll = useCallback(() => {
    if (allFilteredSelected) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(rowsFiltered.map((r) => r.handle_id)));
    }
  }, [allFilteredSelected, rowsFiltered]);

  const clearSelection = useCallback(() => setSelectedIds(new Set()), []);

  const onBulkDeleteSelected = useCallback(async () => {
    if (selectedRows.length === 0) return;
    // Filter to genuinely-deletable rows so the confirm count matches
    // reality — ``deleted`` rows short-circuit the endpoint anyway,
    // but we shouldn't quote them as "will be deleted".
    const deletable = selectedRows.filter((r) => r.state !== "deleted");
    if (deletable.length === 0) {
      alert("Every selected row is already deleted.");
      return;
    }
    const bytes = deletable.reduce(
      (a, r) => a + (r.live_size_bytes ?? r.size_bytes ?? 0),
      0,
    );
    if (
      !window.confirm(
        `Delete ${deletable.length} artifact${deletable.length === 1 ? "" : "s"}` +
          ` (${formatBytes(bytes)})?\n\n` +
          "The DB rows stay for history; only on-disk data is removed.\n" +
          "Rows outside the current workspace roots are tombstoned only —\n" +
          "the file server can't reach them anyway.",
      )
    ) {
      return;
    }
    setBulkBusy(true);
    try {
      // Fan out DELETE calls. The backend's single-delete endpoint
      // already handles every edge case (offline node, path outside
      // roots, already-tombstoned), so we just await them in parallel.
      const results = await Promise.allSettled(
        deletable.map((r) => deleteArtifact(r.handle_id)),
      );
      const errs = results.filter((x) => x.status === "rejected");
      await load({ check: checked });
      if (errs.length > 0) {
        alert(
          `Deleted ${deletable.length - errs.length}/${deletable.length}.\n` +
            `${errs.length} failed — check the state chips.`,
        );
      }
    } finally {
      setBulkBusy(false);
    }
  }, [selectedRows, load, checked]);

  const onCopyRefs = useCallback(async () => {
    if (selectedRows.length === 0) return;
    // One hololab://handle/{id} per line, with a comment carrying the
    // algorithm + port so the pasted output stays human-readable when
    // dropped into a chat/log.
    const lines = selectedRows.map((r) => {
      const comment = [
        r.algorithm_name,
        r.output_port_name,
        r.workflow_name ?? r.workflow_id?.slice(0, 8),
      ]
        .filter(Boolean)
        .join(" · ");
      return comment
        ? `hololab://handle/${r.handle_id}  # ${comment}`
        : `hololab://handle/${r.handle_id}`;
    });
    const text = lines.join("\n");
    const ok = await writeToClipboard(text);
    if (!ok) alert("Copy failed — clipboard is unavailable in this context.");
  }, [selectedRows]);

  // For the workflow filter dropdown: distinct workflows, name-aware.
  const workflows = useMemo(() => {
    if (!data) return [] as Array<{ id: string; name: string; count: number }>;
    const map = new Map<string, { id: string; name: string; count: number }>();
    for (const r of data.rows) {
      if (!r.workflow_id) continue;
      const cur = map.get(r.workflow_id);
      if (cur) {
        cur.count++;
      } else {
        map.set(r.workflow_id, {
          id: r.workflow_id,
          name: r.workflow_name || r.workflow_id.slice(0, 8),
          count: 1,
        });
      }
    }
    return Array.from(map.values()).sort((a, b) => b.count - a.count);
  }, [data]);

  const onDeleteRow = useCallback(
    async (row: ArtifactRow) => {
      if (
        !window.confirm(
          `Delete this artifact from the node's disk?\n\n${row.path}\n\n` +
            `The handle row stays for run history — this only removes the on-disk data.`,
        )
      ) {
        return;
      }
      try {
        await deleteArtifact(row.handle_id);
        await load();
      } catch (e) {
        const detail =
          e instanceof ApiError && typeof e.detail === "object" && e.detail
            ? (e.detail as { detail?: string }).detail
            : null;
        alert(`Delete failed: ${detail ?? (e as Error).message}`);
      }
    },
    [load],
  );

  const onBulkDeleteDead = useCallback(async () => {
    if (!data) return;
    if (!checked) {
      alert(
        'Run "Check liveness" first — bulk cleanup only runs against\n' +
          "handles we've confirmed are dead on disk.",
      );
      return;
    }
    const deadCount = data.rows.filter(
      (r) =>
        r.state === "dead" &&
        (workflowFilter === "all" || r.workflow_id === workflowFilter) &&
        (snapshotFilter === "all" || r.snapshot_id === snapshotFilter),
    ).length;
    if (deadCount === 0) {
      alert("No dead handles in the current filter.");
      return;
    }
    const scopeLabel =
      snapshotFilter !== "all"
        ? " for the selected run"
        : workflowFilter !== "all"
          ? " for the selected workflow"
          : "";
    if (
      !window.confirm(
        `Delete ${deadCount} dead handle${deadCount === 1 ? "" : "s"}${scopeLabel}?\n\n` +
          "The rows stay for history; only on-disk paths are cleaned (they're\n" +
          "already gone by definition).",
      )
    ) {
      return;
    }
    try {
      // Prefer the tightest scope the backend understands: snapshot_id
      // beats workflow_id. If neither is set the bulk endpoint refuses
      // (we'd never intend to sweep across every workflow at once).
      await bulkDeleteArtifacts({
        snapshot_id: snapshotFilter === "all" ? undefined : snapshotFilter,
        workflow_id:
          snapshotFilter !== "all"
            ? undefined
            : workflowFilter === "all"
              ? undefined
              : workflowFilter,
        only_dead: true,
      });
      await load({ check: true });
    } catch (e) {
      alert(`Bulk delete failed: ${(e as Error).message}`);
    }
  }, [data, checked, workflowFilter, snapshotFilter, load]);

  return (
    <div
      className="hl-app-shell"
      style={{
        height: "100vh",
        display: "grid",
        gridTemplateRows: "auto auto 1fr",
        background: "var(--bg)",
        color: "var(--text-body)",
        fontFamily: "var(--font-sans)",
      }}
    >
      <Header
        onBack={onBackToGallery}
        artifactCount={data?.total_rows ?? null}
      />
      <SummaryBar
        data={data}
        loading={loading}
        checked={checked}
        stateFilter={stateFilter}
        setStateFilter={setStateFilter}
        workflowFilter={workflowFilter}
        setWorkflowFilter={setWorkflowFilter}
        workflows={workflows}
        snapshotFilter={snapshotFilter}
        setSnapshotFilter={setSnapshotFilter}
        runs={runs}
        runsLoading={runsLoading}
        onRefresh={() => load()}
        onCheckLiveness={() => load({ check: true })}
        onBulkDeleteDead={onBulkDeleteDead}
      />
      <main className="hl-artifacts-main" style={{ overflow: "auto", padding: "var(--space-6) var(--space-7) var(--space-8)" }}>
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
        {data === null ? (
          <div style={{ color: "var(--text-subtle)", padding: 40, fontSize: "var(--fs-sm)" }}>
            Loading…
          </div>
        ) : rowsFiltered.length === 0 ? (
          <EmptyState hasData={data.total_rows > 0} />
        ) : (
          <ArtifactTable
            rows={rowsFiltered}
            onDelete={onDeleteRow}
            selectedIds={selectedIds}
            allSelected={allFilteredSelected}
            onToggleRow={toggleRow}
            onToggleAll={toggleAll}
          />
        )}
      </main>
      {selectedIds.size > 0 && (
        <BulkActionBar
          count={selectedRows.length}
          bytes={selectedBytes}
          busy={bulkBusy}
          onCopyRefs={onCopyRefs}
          onDelete={onBulkDeleteSelected}
          onClear={clearSelection}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------

function Header({
  onBack,
  artifactCount,
}: {
  onBack: () => void;
  // ``null`` while the artifact list is loading — the ⧉ still emits a
  // valid token in that window (comment tail just omits the count).
  artifactCount: number | null;
}) {
  const comment =
    artifactCount === null
      ? undefined
      : `${artifactCount} artifact${artifactCount === 1 ? "" : "s"}`;
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
      <button
        type="button"
        onClick={onBack}
        title="back to workflows"
        style={{
          ...CONTROL_STYLE,
          fontWeight: 600,
          color: "var(--text)",
          gap: 6,
          letterSpacing: "-0.01em",
          fontFamily: "var(--font-sans)",
        }}
      >
        <span style={{ color: "var(--text-subtle)", fontSize: "var(--fs-md)" }}>‹</span>
        HoloLab
      </button>
      <div style={{ color: "var(--text-muted)" }}>· artifacts</div>
      {/*
        Symmetric with the Gallery header's ⧉: copies
        ``hololab://artifacts  # N artifacts`` so an agent knows the
        user pointed at the artifact inventory as a whole.
      */}
      <CopyRefButton kind="artifacts" id="" comment={comment} size="xs" />
      <div style={{ flex: 1 }} />
      <ThemeToggle />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Summary + actions bar
// ---------------------------------------------------------------------------

function SummaryBar({
  data,
  loading,
  checked,
  stateFilter,
  setStateFilter,
  workflowFilter,
  setWorkflowFilter,
  workflows,
  snapshotFilter,
  setSnapshotFilter,
  runs,
  runsLoading,
  onRefresh,
  onCheckLiveness,
  onBulkDeleteDead,
}: {
  data: ArtifactListResponse | null;
  loading: boolean;
  checked: boolean;
  stateFilter: ArtifactState | "all";
  setStateFilter: (v: ArtifactState | "all") => void;
  workflowFilter: string | "all";
  setWorkflowFilter: (v: string | "all") => void;
  workflows: Array<{ id: string; name: string; count: number }>;
  snapshotFilter: string | "all";
  setSnapshotFilter: (v: string | "all") => void;
  runs: RunSummaryRow[];
  runsLoading: boolean;
  onRefresh: () => void;
  onCheckLiveness: () => void;
  onBulkDeleteDead: () => void;
}) {
  return (
    <div className="hl-artifact-summary"
      style={{
        display: "flex",
        alignItems: "center",
        flexWrap: "wrap",
        gap: 12,
        padding: "12px 24px",
        borderBottom: "1px solid var(--border)",
        background: "var(--surface-2)",
        fontSize: "var(--fs-xs)",
        color: "var(--text-muted)",
      }}
    >
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 8,
        }}
      >
        <span style={{ color: "var(--text-body)" }}>
          <strong style={{ color: "var(--text)" }}>{data?.total_rows ?? "…"}</strong>{" "}
          artifacts
        </span>
        <span>·</span>
        <span>{formatBytes(data?.total_bytes ?? 0)}</span>
      </span>

      {data && (
        <div style={{ display: "flex", gap: 6 }}>
          {STATE_ORDER.filter((s) => (data.counts[s] ?? 0) > 0).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setStateFilter(stateFilter === s ? "all" : s)}
              style={pillStyle(s, stateFilter === s)}
              title={`filter: ${s} · click again to clear`}
            >
              <span
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: "var(--radius-pill)",
                  background: STATE_COLOURS[s],
                }}
              />
              {s}
              <span style={{ marginLeft: 3 }}>{data.counts[s]}</span>
            </button>
          ))}
        </div>
      )}

      <select
        value={workflowFilter}
        onChange={(e) => setWorkflowFilter(e.target.value)}
        style={{
          fontSize: "var(--fs-xs)",
          padding: "3px 6px",
          minWidth: 140,
        }}
      >
        <option value="all">All workflows</option>
        {workflows.map((w) => (
          <option key={w.id} value={w.id}>
            {w.name} ({w.count})
          </option>
        ))}
      </select>

      {/* Chained run filter. Disabled with a placeholder label when no
          workflow is selected — a global "all runs" makes no sense (a
          run belongs to exactly one workflow). */}
      <select
        value={snapshotFilter}
        onChange={(e) => setSnapshotFilter(e.target.value)}
        disabled={workflowFilter === "all" || runsLoading}
        title={
          workflowFilter === "all"
            ? "Pick a workflow first — runs are scoped to their parent workflow."
            : runsLoading
              ? "Loading runs…"
              : "Filter by run (snapshot). Reset when workflow changes."
        }
        style={{
          fontSize: "var(--fs-xs)",
          padding: "3px 6px",
          minWidth: 200,
          opacity: workflowFilter === "all" ? 0.55 : 1,
        }}
      >
        <option value="all">
          {workflowFilter === "all"
            ? "(pick a workflow first)"
            : runsLoading
              ? "Loading runs…"
              : `All runs (${runs.length})`}
        </option>
        {workflowFilter !== "all" &&
          runs.map((r) => (
            <option key={r.snapshot_id} value={r.snapshot_id}>
              {formatRunLabel(r)}
            </option>
          ))}
      </select>

      <div style={{ flex: 1 }} />

      <button type="button" onClick={onRefresh} disabled={loading} style={btnStyle()}>
        {loading ? "Loading…" : "Refresh"}
      </button>
      <button
        type="button"
        onClick={onCheckLiveness}
        disabled={loading}
        style={{
          ...btnStyle(),
          background: checked ? "var(--surface-hover)" : "var(--surface)",
        }}
        title={
          checked
            ? "Liveness has been checked. Click to re-check."
            : "Ask each producing node whether its artifact files still exist."
        }
      >
        {checked ? "Re-check liveness" : "Check liveness"}
      </button>
      <button
        type="button"
        onClick={onBulkDeleteDead}
        disabled={loading || !checked}
        title={
          checked
            ? "Delete every handle currently classified as dead in the filter."
            : "Enable by running Check liveness first."
        }
        style={{
          ...btnStyle(),
          borderColor: "var(--error)",
          color: "var(--error)",
          opacity: !checked ? 0.6 : 1,
        }}
      >
        Delete all dead
      </button>
    </div>
  );
}

function pillStyle(_state: ArtifactState, active: boolean): React.CSSProperties {
  return {
    display: "inline-flex",
    alignItems: "center",
    gap: 5,
    fontSize: "var(--fs-xs)",
    fontFamily: "var(--font-sans)",
    color: active ? "var(--text)" : "var(--text-muted)",
    background: active ? "var(--surface)" : "transparent",
    border: `1px solid ${active ? "var(--border-strong)" : "var(--border)"}`,
    borderRadius: "var(--radius-pill)",
    padding: "2px 10px",
    cursor: "pointer",
    fontVariantNumeric: "tabular-nums",
  };
}

function btnStyle(): React.CSSProperties {
  return {
    ...CONTROL_STYLE,
  };
}

// ---------------------------------------------------------------------------
// Table
// ---------------------------------------------------------------------------

// Grid template used by both the header and each row so the columns
// stay aligned. Leading 24px is the multi-select checkbox column.
const TABLE_COLS = "24px 70px 140px minmax(180px, 1fr) 90px 100px 120px 40px";

function ArtifactTable({
  rows,
  onDelete,
  selectedIds,
  allSelected,
  onToggleRow,
  onToggleAll,
}: {
  rows: ArtifactRow[];
  onDelete: (row: ArtifactRow) => void;
  selectedIds: Set<string>;
  allSelected: boolean;
  onToggleRow: (handle_id: string) => void;
  onToggleAll: () => void;
}) {
  return (
    <div className="hl-artifact-table"
      style={{
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-md)",
        background: "var(--surface)",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          display: "grid",
          gridTemplateColumns: TABLE_COLS,
          gap: 12,
          padding: "10px 14px",
          borderBottom: "1px solid var(--border)",
          background: "var(--surface-2)",
          fontSize: 10,
          fontWeight: 600,
          letterSpacing: "0.08em",
          textTransform: "uppercase",
          color: "var(--text-subtle)",
          alignItems: "center",
        }}
      >
        <input
          type="checkbox"
          checked={allSelected}
          onChange={onToggleAll}
          title={
            allSelected
              ? "Deselect every row in the current filter."
              : "Select every row in the current filter."
          }
          style={{ margin: 0, cursor: "pointer" }}
        />
        <span>State</span>
        <span>Algorithm</span>
        <span>Path</span>
        <span>Size</span>
        <span>Created</span>
        <span>Workflow</span>
        <span></span>
      </div>
      {rows.map((r) => (
        <ArtifactRowView
          key={r.handle_id}
          row={r}
          onDelete={onDelete}
          selected={selectedIds.has(r.handle_id)}
          onToggle={() => onToggleRow(r.handle_id)}
        />
      ))}
    </div>
  );
}

function ArtifactRowView({
  row,
  onDelete,
  selected,
  onToggle,
}: {
  row: ArtifactRow;
  onDelete: (row: ArtifactRow) => void;
  selected: boolean;
  onToggle: () => void;
}) {
  const canDelete = row.state !== "deleted";
  const alreadyDead = row.state === "dead";
  // Selected rows get a persistent tint so the bulk action bar's
  // "N selected" always maps back to visible rows at a glance.
  const restingBg = selected ? "var(--accent-soft)" : "transparent";
  const hoverBg = selected ? "var(--accent-soft)" : "var(--surface-hover)";
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: TABLE_COLS,
        gap: 12,
        padding: "10px 14px",
        borderBottom: "1px solid var(--border)",
        fontSize: "var(--fs-xs)",
        color: "var(--text-body)",
        alignItems: "center",
        transition: "background var(--dur-fast) var(--ease)",
        background: restingBg,
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.background = hoverBg;
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = restingBg;
      }}
    >
      <input
        type="checkbox"
        checked={selected}
        onChange={onToggle}
        onClick={(e) => e.stopPropagation()}
        title={selected ? "Deselect this row" : "Select this row"}
        style={{ margin: 0, cursor: "pointer" }}
      />
      <StateChip state={row.state} />
      <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        <span style={{ fontWeight: 500, color: "var(--text)" }}>
          {row.algorithm_name ?? "—"}
        </span>
        {row.algorithm_version && (
          <span
            style={{
              color: "var(--text-subtle)",
              marginLeft: 4,
              fontFamily: "var(--font-mono)",
            }}
          >
            v{row.algorithm_version}
          </span>
        )}
        {row.output_port_name && (
          <div style={{ color: "var(--text-subtle)", fontFamily: "var(--font-mono)" }}>
            {row.output_port_name}
          </div>
        )}
      </div>
      <code
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: "var(--fs-micro)",
          color: "var(--text-muted)",
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
        title={row.path}
      >
        {row.path}
      </code>
      <span
        style={{
          fontVariantNumeric: "tabular-nums",
          color: alreadyDead ? "var(--text-subtle)" : "var(--text-body)",
        }}
      >
        {formatBytes(row.live_size_bytes ?? row.size_bytes ?? 0)}
      </span>
      <span style={{ color: "var(--text-muted)", fontVariantNumeric: "tabular-nums" }}>
        {formatRelative(row.created_ts)}
      </span>
      <span
        style={{ color: "var(--text-muted)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}
        title={row.workflow_name ?? row.workflow_id ?? ""}
      >
        {row.workflow_name ?? (row.workflow_id ? row.workflow_id.slice(0, 8) : "—")}
      </span>
      {canDelete ? (
        <button
          className="hl-control-sm"
          type="button"
          onClick={() => onDelete(row)}
          title="Delete artifact from disk"
          style={{
            border: "1px solid var(--border)",
            background: "transparent",
            color: "var(--text-muted)",
            padding: "2px 6px",
            borderRadius: "var(--radius-sm)",
            cursor: "pointer",
            fontSize: 13,
            lineHeight: 1,
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = "var(--error-soft)";
            e.currentTarget.style.color = "var(--error)";
            e.currentTarget.style.borderColor = "var(--error)";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = "transparent";
            e.currentTarget.style.color = "var(--text-muted)";
            e.currentTarget.style.borderColor = "var(--border)";
          }}
        >
          ×
        </button>
      ) : (
        <span
          style={{
            color: "var(--text-subtle)",
            textAlign: "center",
            fontSize: 12,
          }}
          title="already deleted"
        >
          ·
        </span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Bulk-actions bar — appears when the user has picked at least one row.
//
// Floating at the bottom-centre so a full-viewport table still has a clear
// action target. Fixed positioning keeps it in place while the row list
// scrolls behind it. Deliberately tight action set:
//   * Copy N refs — writes ``hololab://handle/{id}`` per line to the
//     clipboard. Consistent with the CopyRefButton scheme used in
//     RunsPanel / SnapshotBanner.
//   * Delete N files — same semantics as the single-row × delete but in
//     a Promise.allSettled fan-out. Node-side path guard is unchanged.
//   * Clear — dismiss without any action.
// ---------------------------------------------------------------------------

function BulkActionBar({
  count,
  bytes,
  busy,
  onCopyRefs,
  onDelete,
  onClear,
}: {
  count: number;
  bytes: number;
  busy: boolean;
  onCopyRefs: () => void;
  onDelete: () => void;
  onClear: () => void;
}) {
  return (
    <div
      role="toolbar"
      aria-label="Bulk actions on selected artifacts"
      style={{
        position: "fixed",
        left: "50%",
        bottom: 24,
        transform: "translateX(-50%)",
        display: "inline-flex",
        alignItems: "center",
        gap: 12,
        padding: "8px 12px",
        background: "var(--surface)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-md)",
        boxShadow: "var(--shadow-2)",
        fontSize: "var(--fs-xs)",
        color: "var(--text-body)",
        zIndex: 40,
      }}
    >
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 8,
          padding: "0 6px",
        }}
      >
        <strong style={{ color: "var(--text)" }}>{count}</strong>
        selected
        <span style={{ color: "var(--text-subtle)" }}>·</span>
        <span style={{ fontVariantNumeric: "tabular-nums" }}>{formatBytes(bytes)}</span>
      </span>
      <div
        style={{
          width: 1,
          height: 18,
          background: "var(--border)",
        }}
      />
      <button
        type="button"
        onClick={onCopyRefs}
        disabled={busy}
        title="Copy hololab://handle/… references for the selected rows to the clipboard, one per line."
        style={btnStyle()}
      >
        Copy {count} ref{count === 1 ? "" : "s"}
      </button>
      <button
        type="button"
        onClick={onDelete}
        disabled={busy}
        style={{
          ...btnStyle(),
          borderColor: "var(--error)",
          color: "var(--error)",
          opacity: busy ? 0.6 : 1,
        }}
      >
        {busy ? "Deleting…" : `Delete ${count} file${count === 1 ? "" : "s"}`}
      </button>
      <button
        type="button"
        onClick={onClear}
        disabled={busy}
        title="Clear the selection without deleting anything."
        style={{
          border: "none",
          background: "transparent",
          color: "var(--text-muted)",
          padding: "4px 8px",
          borderRadius: "var(--radius-sm)",
          cursor: "pointer",
          fontSize: "var(--fs-xs)",
          fontFamily: "var(--font-sans)",
        }}
      >
        Clear
      </button>
    </div>
  );
}

function StateChip({ state }: { state: ArtifactState }) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 5,
        fontSize: 10,
        fontWeight: 600,
        letterSpacing: "0.04em",
        textTransform: "uppercase",
        color: "var(--text-body)",
      }}
    >
      <span
        style={{
          width: 7,
          height: 7,
          borderRadius: "var(--radius-pill)",
          background: STATE_COLOURS[state],
        }}
      />
      {state}
    </span>
  );
}

function EmptyState({ hasData }: { hasData: boolean }) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        color: "var(--text-muted)",
        padding: "80px 20px",
        gap: 12,
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
        {hasData ? "No artifacts match the filter" : "No artifacts yet"}
      </div>
      <div
        style={{
          maxWidth: 460,
          textAlign: "center",
          fontSize: "var(--fs-sm)",
          lineHeight: 1.5,
        }}
      >
        {hasData
          ? "Adjust the state / workflow filter above."
          : "Every completed job registers its outputs here. Run a workflow to see them show up."}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

function formatBytes(n: number): string {
  if (n <= 0) return "0 B";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  const v = n / Math.pow(1024, i);
  return `${v >= 100 ? v.toFixed(0) : v.toFixed(1)} ${units[i]}`;
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

// Clipboard writer with a document.execCommand fallback — the app runs
// over plain http on the LAN and navigator.clipboard is gated on
// isSecureContext, so we need the legacy path too. Kept local rather
// than re-imported so the Artifacts page doesn't depend on the
// CopyRefButton implementation detail.
async function writeToClipboard(text: string): Promise<boolean> {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      /* fall through */
    }
  }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  document.body.removeChild(ta);
  return ok;
}

// One-line label for a run in the chained filter dropdown. Mirrors the
// shape used in RunsPanel so the same run is visually recognisable
// across the two views. ``<option>`` can't nest coloured pips so we
// substitute a leading state marker glyph.
function formatRunLabel(r: RunSummaryRow): string {
  const d = new Date(r.created_ts * 1000);
  const ts = `${d.toISOString().slice(0, 10)} ${d.toISOString().slice(11, 16)}`;
  const glyph =
    r.state === "done"
      ? "✓"
      : r.state === "failed"
        ? "✗"
        : r.state === "running"
          ? "…"
          : "·";
  return `${glyph} ${ts} · ${r.job_count} jobs · ${r.snapshot_id.slice(0, 8)}`;
}
