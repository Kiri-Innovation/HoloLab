// Read-only variant of NodeInspector for the snapshot canvas view.
//
// Mounts into the same bottom slot NodeInspector uses on the draft view,
// so the visual rhythm of "select a node → its detail appears at the
// bottom" carries across into past-run inspection. But here every field
// is frozen: params, input handles, output handles, plus the run outcome
// (state, progress, fail reason).
//
// Layout mirrors NodeInspector: left column = pack docs (clamped preview
// + "View full" modal), right column = everything else and scrolls. When
// the panel is dragged narrow the docs column collapses into an "ABOUT"
// chip inline with the Node heading.

import { useEffect, useRef, useState, type CSSProperties } from "react";
import type { CatalogPack, SnapshotJob } from "../wire";
import { stateColour } from "./AlgorithmNode";
import { MarkdownView } from "../ui/MarkdownView";
import { Modal } from "../ui/Modal";

export interface SnapshotNodeInspectorProps {
  // The graph node the user selected on the read-only canvas. When
  // ``null`` we show the empty-state hint so the slot doesn't look broken.
  graphNodeId: string | null;
  // The pack for that graph node, resolved from the catalog. Might be
  // null if the pack has since been removed — we show a placeholder.
  pack: CatalogPack | null;
  // The job (if any) that ran for this graph node in the selected
  // snapshot. Missing = the run stopped before this node dispatched, or
  // the node was never wired to a run.
  job: SnapshotJob | null;
  // Rerun-from action. Fired when the user clicks "Re-run from here" —
  // creates a new snapshot with reused upstream and re-executed
  // downstream, then navigates the App to view it.
  onRerunFromHere?: (graphNodeId: string) => Promise<void>;
}

const LABEL: CSSProperties = {
  fontSize: 10,
  color: "var(--text-subtle)",
  textTransform: "uppercase",
  letterSpacing: "0.08em",
  fontWeight: 600,
  marginBottom: 6,
};

const CODE: CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: "var(--fs-xs)",
  color: "var(--text-body)",
  background: "var(--surface-2)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-sm)",
  padding: "8px 10px",
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
  margin: 0,
  lineHeight: 1.5,
};

// Kept in sync with NodeInspector so both surfaces behave identically at
// the same panel widths / heights.
const DOCS_CLAMP_PX = 140;
const NARROW_WIDTH_PX = 520;

export function SnapshotNodeInspector({
  graphNodeId,
  pack,
  job,
  onRerunFromHere,
}: SnapshotNodeInspectorProps) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [narrow, setNarrow] = useState(false);
  const [docsOpen, setDocsOpen] = useState(false);
  const [rerunning, setRerunning] = useState(false);
  const [rerunErr, setRerunErr] = useState<string | null>(null);

  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) setNarrow(e.contentRect.width < NARROW_WIDTH_PX);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const doRerun = async () => {
    if (!onRerunFromHere || !graphNodeId) return;
    if (
      !window.confirm(
        `Re-run from this node?\n\nUpstream nodes will reuse their existing outputs.\nThis node (${
          job?.algorithm_name ?? pack?.name ?? graphNodeId
        }) and everything downstream will re-execute.`,
      )
    ) {
      return;
    }
    setRerunning(true);
    setRerunErr(null);
    try {
      await onRerunFromHere(graphNodeId);
    } catch (e) {
      setRerunErr((e as Error).message);
    } finally {
      setRerunning(false);
    }
  };

  if (!graphNodeId) {
    return (
      <div
        ref={rootRef}
        style={{
          padding: "18px 20px",
          color: "var(--text-muted)",
          fontSize: "var(--fs-sm)",
          height: "100%",
          lineHeight: 1.5,
          maxWidth: 420,
        }}
      >
        Select a node on the read-only canvas to see the parameters and
        outputs frozen at run time.
      </div>
    );
  }

  const hasDocs = !!pack?.docs && pack.docs.trim().length > 0;
  const showLeftColumn = hasDocs && !narrow;

  return (
    <div
      ref={rootRef}
      style={{
        padding: "14px 20px 20px",
        height: "100%",
        fontSize: "var(--fs-sm)",
        color: "var(--text-body)",
        display: "grid",
        gridTemplateColumns: showLeftColumn ? "minmax(220px, 300px) 1fr" : "1fr",
        columnGap: 24,
        overflow: "hidden",
        minHeight: 0,
      }}
    >
      {showLeftColumn && (
        <DocsColumn docs={pack!.docs!} onOpenFull={() => setDocsOpen(true)} />
      )}

      <div
        style={{
          minWidth: 0,
          minHeight: 0,
          overflowY: "auto",
          paddingRight: 4,
          display: "grid",
          gridTemplateColumns: "minmax(220px, 300px) 1fr",
          columnGap: 24,
        }}
      >
        <div style={{ minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, marginBottom: 6 }}>
            <div style={LABEL}>Node</div>
            {hasDocs && narrow && (
              <button
                type="button"
                onClick={() => setDocsOpen(true)}
                title="Show pack docs"
                style={{
                  background: "transparent",
                  border: "1px solid var(--border)",
                  color: "var(--text-muted)",
                  fontSize: 10,
                  fontWeight: 600,
                  letterSpacing: "0.08em",
                  textTransform: "uppercase",
                  padding: "3px 8px",
                  borderRadius: "var(--radius-sm)",
                  cursor: "pointer",
                }}
              >
                About
              </button>
            )}
          </div>
          <div style={{ marginBottom: 14 }}>
            {pack ? (
              <>
                <strong style={{ color: "var(--text)", fontWeight: 600 }}>{pack.name}</strong>
                <span
                  style={{
                    color: "var(--text-muted)",
                    marginLeft: 6,
                    fontFamily: "var(--font-mono)",
                    fontSize: "var(--fs-xs)",
                  }}
                >
                  v{pack.version}
                </span>
              </>
            ) : (
              <span style={{ color: "var(--text-subtle)" }}>
                pack no longer available
                {job && (
                  <>
                    {" — was "}
                    {job.algorithm_name}@{job.algorithm_version}
                  </>
                )}
              </span>
            )}
            <div
              style={{
                marginTop: 4,
                fontFamily: "var(--font-mono)",
                fontSize: "var(--fs-micro)",
                color: "var(--text-subtle)",
              }}
            >
              {graphNodeId}
            </div>
          </div>

          <div style={LABEL}>State</div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 16 }}>
            {job ? (
              <>
                <span
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: "var(--radius-pill)",
                    background: stateColour(job.state),
                  }}
                />
                <span style={{ fontWeight: 500 }}>{job.state}</span>
                {job.progress && job.progress.total > 0 && (
                  <span
                    style={{
                      color: "var(--text-muted)",
                      fontSize: "var(--fs-xs)",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {job.progress.current}/{job.progress.total}
                  </span>
                )}
              </>
            ) : (
              <span style={{ color: "var(--text-subtle)" }}>no job dispatched</span>
            )}
          </div>

          {onRerunFromHere && (
            <>
              <div style={LABEL}>Actions</div>
              <div style={{ marginBottom: 16 }}>
                <button
                  type="button"
                  onClick={doRerun}
                  disabled={rerunning}
                  title={
                    job?.state === "done"
                      ? "Re-run this node and everything downstream. Upstream outputs are reused."
                      : "Retry this node and its downstream. Upstream done nodes will be reused."
                  }
                  style={{
                    border: "1px solid var(--accent)",
                    background: rerunning ? "var(--accent-soft)" : "transparent",
                    color: "var(--accent)",
                    padding: "5px 12px",
                    borderRadius: "var(--radius-sm)",
                    cursor: rerunning ? "wait" : "pointer",
                    fontSize: "var(--fs-xs)",
                    fontWeight: 600,
                  }}
                >
                  ↻ {rerunning ? "starting…" : "Re-run from here"}
                </button>
                {rerunErr && (
                  <div
                    style={{
                      marginTop: 6,
                      fontSize: "var(--fs-xs)",
                      color: "var(--error)",
                    }}
                  >
                    {rerunErr}
                  </div>
                )}
              </div>
            </>
          )}

          {job?.fail_reason && (
            <>
              <div style={LABEL}>Failure</div>
              <div
                style={{
                  marginBottom: 16,
                  fontSize: "var(--fs-xs)",
                  color: "var(--error)",
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                  lineHeight: 1.5,
                }}
              >
                {job.fail_reason}
                {job.fail_exit_code != null && (
                  <span style={{ color: "var(--text-subtle)" }}>
                    {" "}
                    · exit {job.fail_exit_code}
                  </span>
                )}
                {job.fail_message && (
                  <div style={{ marginTop: 6, color: "var(--text-body)" }}>
                    {job.fail_message}
                  </div>
                )}
              </div>
            </>
          )}
        </div>

        <div style={{ minWidth: 0 }}>
          <div style={LABEL}>Frozen params</div>
          <pre style={{ ...CODE, marginBottom: 12 }}>
            {job && Object.keys(job.params).length > 0
              ? JSON.stringify(job.params, null, 2)
              : "(none)"}
          </pre>

          <div style={LABEL}>Input handles</div>
          <pre style={{ ...CODE, marginBottom: 12 }}>
            {job && Object.keys(job.input_handles).length > 0
              ? JSON.stringify(job.input_handles, null, 2)
              : "(none)"}
          </pre>

          <div style={LABEL}>Output handles</div>
          {job?.output_handles && Object.keys(job.output_handles).length > 0 ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {Object.entries(job.output_handles).map(([port, hid]) => {
                const st = job.output_handle_states?.[port] ?? "pending";
                const deleted = st === "deleted";
                return (
                  <div
                    key={port}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 8,
                      fontSize: "var(--fs-xs)",
                      color: deleted ? "var(--text-subtle)" : "var(--text-body)",
                      fontFamily: "var(--font-mono)",
                      padding: "6px 8px",
                      background: "var(--surface-2)",
                      border: "1px solid var(--border)",
                      borderRadius: "var(--radius-sm)",
                    }}
                  >
                    <OutputPortBadge state={st} />
                    <span style={{ fontWeight: 500 }}>{port}</span>
                    <span style={{ color: "var(--text-subtle)" }}>·</span>
                    <span
                      style={{
                        color: "var(--text-muted)",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                      }}
                      title={hid}
                    >
                      {hid.slice(0, 8)}
                    </span>
                  </div>
                );
              })}
            </div>
          ) : (
            <pre style={CODE}>(none)</pre>
          )}
        </div>
      </div>

      {hasDocs && (
        <Modal
          open={docsOpen}
          onClose={() => setDocsOpen(false)}
          title={`About · ${pack!.name}@${pack!.version}`}
        >
          <MarkdownView source={pack!.docs!} />
        </Modal>
      )}
    </div>
  );
}

function DocsColumn({ docs, onOpenFull }: { docs: string; onOpenFull: () => void }) {
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const [overflow, setOverflow] = useState(false);

  useEffect(() => {
    const el = bodyRef.current;
    if (!el) return;
    const check = () => setOverflow(el.scrollHeight > el.clientHeight + 1);
    check();
    const ro = new ResizeObserver(check);
    ro.observe(el);
    return () => ro.disconnect();
  }, [docs]);

  return (
    <div style={{ minWidth: 0, display: "flex", flexDirection: "column" }}>
      <div style={LABEL}>About</div>
      <div style={{ position: "relative" }}>
        <div
          ref={bodyRef}
          style={{
            maxHeight: DOCS_CLAMP_PX,
            overflow: "hidden",
            maskImage: overflow
              ? "linear-gradient(to bottom, black 78%, transparent)"
              : undefined,
            WebkitMaskImage: overflow
              ? "linear-gradient(to bottom, black 78%, transparent)"
              : undefined,
          }}
        >
          <MarkdownView source={docs} />
        </div>
      </div>
      {overflow && (
        <button
          type="button"
          onClick={onOpenFull}
          style={{
            alignSelf: "flex-start",
            marginTop: 6,
            background: "transparent",
            border: "none",
            padding: 0,
            color: "var(--accent)",
            fontSize: "var(--fs-xs)",
            fontWeight: 500,
            cursor: "pointer",
            textDecoration: "underline",
          }}
        >
          View full
        </button>
      )}
    </div>
  );
}

// Per-port liveness pill. ``deleted`` shows a struck-out subtle gray so
// the row is visibly out of commission; ``pending`` is a neutral pip.
// Live check states (``alive``/``incomplete``/``dead``) will land here
// once the SnapshotNodeInspector opts into the ?check=1 pathway — for
// now the DB-cheap two-state view is what the endpoint returns.
function OutputPortBadge({ state }: { state: string }) {
  const styleMap: Record<string, { fg: string; bg: string; label: string }> = {
    deleted: {
      fg: "var(--text-subtle)",
      bg: "var(--surface-alt)",
      label: "deleted",
    },
    pending: {
      fg: "var(--text-muted)",
      bg: "var(--surface-alt)",
      label: "ok",
    },
    alive: {
      fg: "var(--success)",
      bg: "var(--success-soft)",
      label: "alive",
    },
    incomplete: {
      fg: "var(--warning)",
      bg: "var(--warning-soft)",
      label: "incomplete",
    },
    dead: {
      fg: "var(--error)",
      bg: "var(--error-soft)",
      label: "missing",
    },
  };
  const s = styleMap[state] ?? styleMap.pending;
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: "1px 6px",
        borderRadius: "var(--radius-pill)",
        background: s.bg,
        color: s.fg,
        fontSize: 9,
        fontWeight: 600,
        letterSpacing: "0.04em",
        textTransform: "uppercase",
        fontFamily: "var(--font-sans)",
      }}
    >
      {s.label}
    </span>
  );
}
