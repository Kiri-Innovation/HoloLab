// Bottom panel: params + compute-node assignment for the selected algorithm
// node on the canvas.
//
// Layout: two columns.
//   * Left  = pack docs (Markdown, clamped to a preview with "View full")
//   * Right = compute-node picker + params (scrolls when tall)
//
// Left is fixed and never scrolls — you either read the preview and move
// on, or click "View full" to open the modal. Right owns any overflow;
// if a pack has 30 params the left column stays put while the right list
// scrolls. When the panel is dragged narrow the left column collapses into
// a small "ABOUT" chip in the right column's header row.

import { useEffect, useRef, useState, type CSSProperties } from "react";
import type { CatalogPack, ComputeNode, GraphNode } from "../wire";
import { MarkdownView } from "../ui/MarkdownView";
import { Modal } from "../ui/Modal";

export interface NodeInspectorProps {
  selected: GraphNode | null;
  pack: CatalogPack | null;
  computeNodes: ComputeNode[];
  onChange: (patch: Partial<GraphNode>) => void;
  onDelete: () => void;
}

const SECTION_TITLE: CSSProperties = {
  fontSize: 10,
  fontWeight: 600,
  letterSpacing: "0.08em",
  textTransform: "uppercase",
  color: "var(--text-subtle)",
  margin: "0 0 10px",
};

const FIELD: CSSProperties = { marginBottom: 12 };

const LABEL: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
  fontSize: "var(--fs-xs)",
  fontWeight: 500,
  color: "var(--text-body)",
  marginBottom: 4,
};

const HINT: CSSProperties = {
  fontSize: "var(--fs-xs)",
  color: "var(--text-muted)",
  marginTop: 4,
  lineHeight: 1.4,
};

const TAG: CSSProperties = {
  fontSize: 9.5,
  fontWeight: 600,
  letterSpacing: "0.03em",
  textTransform: "uppercase",
  padding: "1px 5px",
  borderRadius: "var(--radius-sm)",
  background: "var(--surface-alt)",
  color: "var(--text-muted)",
};

// Roughly 6 lines of body copy at 13px/1.5. Sized for the inspector's
// default height; when the panel is dragged shorter the outer container
// crops further — clamp is the cap, not a promise.
const DOCS_CLAMP_PX = 140;

// Below this container width the left column collapses into a chip in
// the right column's header row.
const NARROW_WIDTH_PX = 520;

export function NodeInspector({
  selected,
  pack,
  computeNodes,
  onChange,
  onDelete,
}: NodeInspectorProps) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [narrow, setNarrow] = useState(false);
  const [docsOpen, setDocsOpen] = useState(false);

  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) setNarrow(e.contentRect.width < NARROW_WIDTH_PX);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  if (!selected || !pack) {
    return (
      <div
        className="hl-inspector-empty"
        ref={rootRef}
        style={{
          padding: "18px 20px",
          color: "var(--text-muted)",
          fontSize: "var(--fs-sm)",
          height: "100%",
          display: "flex",
          alignItems: "flex-start",
        }}
      >
        <span style={{ maxWidth: 420, lineHeight: 1.5 }}>
          Select a node on the canvas to edit its params and compute-node assignment.
        </span>
      </div>
    );
  }

  const eligible = computeNodes.filter((cn) =>
    cn.packs.some((p) => p.name === pack.name && p.version === pack.version),
  );

  const hasDocs = !!pack.docs && pack.docs.trim().length > 0;
  const showLeftColumn = hasDocs && !narrow;

  return (
    <div
      className="hl-node-inspector"
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
        <DocsColumn docs={pack.docs!} onOpenFull={() => setDocsOpen(true)} />
      )}

      <div
        style={{
          minWidth: 0,
          minHeight: 0,
          overflowY: "auto",
          paddingRight: 4,
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginBottom: 12,
            gap: 12,
          }}
        >
          <div style={{ minWidth: 0, display: "flex", alignItems: "baseline", gap: 8 }}>
            <div
              style={{
                fontSize: "var(--fs-lg)",
                fontWeight: 600,
                lineHeight: 1.2,
                color: "var(--text)",
                letterSpacing: "-0.005em",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {pack.name}
            </div>
            <div
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "var(--fs-xs)",
                color: "var(--text-muted)",
              }}
            >
              v{pack.version}
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
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
                  padding: "4px 8px",
                  borderRadius: "var(--radius-sm)",
                  cursor: "pointer",
                }}
              >
                About
              </button>
            )}
            <button
              onClick={onDelete}
              style={{
                background: "transparent",
                border: "1px solid var(--border)",
                color: "var(--error)",
                fontSize: "var(--fs-xs)",
                fontWeight: 500,
                padding: "4px 10px",
                borderRadius: "var(--radius-sm)",
                cursor: "pointer",
                transition:
                  "background var(--dur-fast) var(--ease), border-color var(--dur-fast) var(--ease)",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = "var(--error-soft)";
                e.currentTarget.style.borderColor = "var(--error)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = "transparent";
                e.currentTarget.style.borderColor = "var(--border)";
              }}
            >
              Delete node
            </button>
          </div>
        </div>

        <h3 style={SECTION_TITLE}>Compute node</h3>
        <div style={FIELD}>
          <div style={LABEL}>
            Assigned node
            {selected.assigned_node_id ? null : <span style={TAG}>unassigned</span>}
          </div>
          <select
            value={selected.assigned_node_id || ""}
            onChange={(e) =>
              onChange({ assigned_node_id: e.target.value || null })
            }
            style={{ width: "100%" }}
          >
            <option value="">— unassigned —</option>
            {eligible.map((cn) => (
              <option key={cn.node_id} value={cn.node_id}>
                {cn.node_name} · {cn.node_id.slice(0, 6)}
              </option>
            ))}
          </select>
          {eligible.length === 0 ? (
            <div style={{ ...HINT, color: "var(--error)" }}>
              No online compute node offers this pack.
            </div>
          ) : (
            <div style={HINT}>
              Only compute nodes serving {pack.name}@{pack.version} are listed.
            </div>
          )}
        </div>

        <h3 style={{ ...SECTION_TITLE, marginTop: 18 }}>Parameters</h3>
        {Object.keys(pack.params).length === 0 ? (
          <div style={{ ...HINT, color: "var(--text-subtle)" }}>
            This pack takes no parameters.
          </div>
        ) : (
          Object.entries(pack.params).map(([name, spec]) => {
            const current =
              (selected.params[name] as string | number | boolean | undefined) ??
              (spec.default as string | number | boolean | undefined) ??
              "";
            const isNumeric = spec.type === "int" || spec.type === "float";
            return (
              <div key={name} style={FIELD}>
                <div style={LABEL}>
                  <code style={{ fontFamily: "var(--font-mono)" }}>{name}</code>
                  <span style={TAG}>{spec.type}</span>
                  {spec.optional ? (
                    <span style={{ ...TAG, color: "var(--text-subtle)" }}>optional</span>
                  ) : null}
                </div>
                <input
                  value={String(current)}
                  onChange={(e) => {
                    const raw = e.target.value;
                    const parsed = coerce(raw, spec.type);
                    onChange({
                      params: { ...selected.params, [name]: parsed },
                    });
                  }}
                  style={{
                    width: "100%",
                    fontFamily: isNumeric ? "var(--font-mono)" : "inherit",
                    fontVariantNumeric: isNumeric ? "tabular-nums" : "normal",
                    textAlign: isNumeric ? "right" : "left",
                  }}
                />
                {spec.description && <div style={HINT}>{spec.description}</div>}
              </div>
            );
          })
        )}
      </div>

      {hasDocs && (
        <Modal
          open={docsOpen}
          onClose={() => setDocsOpen(false)}
          title={`About · ${pack.name}@${pack.version}`}
        >
          <MarkdownView source={pack.docs!} />
        </Modal>
      )}
    </div>
  );
}

// Left column: docs preview clamped to a fixed max height with a soft fade
// at the bottom when content overflows, plus a "View full" affordance that
// opens the modal.
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
      <h3 style={SECTION_TITLE}>About</h3>
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

function coerce(raw: string, type: string): unknown {
  if (type === "int") {
    const n = Number.parseInt(raw, 10);
    return Number.isFinite(n) ? n : raw;
  }
  if (type === "float") {
    const n = Number.parseFloat(raw);
    return Number.isFinite(n) ? n : raw;
  }
  if (type === "bool") return raw === "true" || raw === "1";
  return raw;
}
