// Compact right-sidebar parameter editor. Changes use the existing draft autosave.

import { useState, type CSSProperties } from "react";
import type { CatalogPack, GraphNode } from "../wire";
import { MarkdownView } from "../ui/MarkdownView";
import { Modal } from "../ui/Modal";
import { PathInput } from "./PathInput";
import type { ComputeNode } from "../wire";
import { DimListEditor } from "./DimListEditor";

export interface NodeInspectorProps {
  selected: GraphNode | null;
  pack: CatalogPack | null;
  computeNodesById?: Record<string, ComputeNode>;
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

const FIELD: CSSProperties = { marginBottom: 8 };

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

export function NodeInspector({
  selected,
  pack,
  computeNodesById,
  onChange,
  onDelete,
}: NodeInspectorProps) {
  const [docsOpen, setDocsOpen] = useState(false);

  if (!selected || !pack) {
    return (
      <div
        className="hl-inspector-empty"
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

  const hasDocs = !!pack.docs && pack.docs.trim().length > 0;

  return (
    <div
      className="hl-node-inspector"
      style={{
        padding: "14px",
        height: "100%",
        fontSize: "var(--fs-sm)",
        color: "var(--text-body)",
        display: "grid",
        gridTemplateColumns: "minmax(0, 1fr)",
        columnGap: 24,
        overflow: "hidden",
        minHeight: 0,
      }}
    >
      <div
        style={{
          minWidth: 0,
          minHeight: 0,
          overflowY: "auto",
          paddingRight: 4,
        }}
      >
        <div
          className="hl-inspector-heading"
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
            {hasDocs && (
              <button
                type="button"
                onClick={() => setDocsOpen(true)}
                data-tooltip="查看文档" aria-label="About" aria-description="Show pack docs"
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

        {pack.arrayable && (
          <>
            <h3 style={{ ...SECTION_TITLE, marginTop: 18 }}>Fan-out</h3>
            <div style={FIELD}>
              <label
                style={{
                  ...LABEL,
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "flex-start",
                  gap: 8,
                }}
              >
                <input
                  type="checkbox"
                  data-hl-arrayed-toggle=""
                  checked={Boolean(selected.arrayed_toggle)}
                  onChange={(e) => onChange({ arrayed_toggle: e.target.checked })}
                  style={{ marginTop: 2 }}
                />
                <div>
                  <div style={{ fontWeight: 500 }}>并行处理数组输入</div>
                  <details className="hl-field-help"><summary>How fan-out works</summary><div style={{ ...HINT, marginTop: 2 }}>
                    勾选后此节点的输入/输出端口变为 <code>arrayed&lt;T&gt;</code>；
                    运行时框架对每个数组元素起一个 sub-job，
                    产物聚合在 <code>{"{parent}/{port}/{element_id}/"}</code>。
                  </div></details>
                </div>
              </label>
            </div>
            {selected.arrayed_toggle ? (
              <div style={FIELD}>
                <div style={LABEL}>
                  <span>并行执行数</span>
                  <span style={TAG}>structural</span>
                </div>
                <input
                  type="number"
                  min={1}
                  max={8}
                  step={1}
                  data-hl-parallelism=""
                  value={Math.max(1, Math.min(8, Number(selected.parallelism ?? 1)))}
                  onChange={(e) => {
                    const raw = Number(e.target.value);
                    const clamped = Number.isFinite(raw)
                      ? Math.max(1, Math.min(8, Math.round(raw)))
                      : 1;
                    onChange({ parallelism: clamped });
                  }}
                  style={{
                    width: 72,
                    fontFamily: "var(--font-mono)",
                    fontVariantNumeric: "tabular-nums",
                    textAlign: "left",
                  }}
                />
                <details className="hl-field-help"><summary>Help</summary><div style={{ ...HINT, marginTop: 4 }}>
                  同时派发的 shard 上限（1–8，默认 1 = 串行）。
                  实际并发 = min(本值, 节点守护进程 <code>max_concurrent_jobs</code>)。
                  改动会 Fork 新的快照。
                </div></details>
              </div>
            ) : null}
            {selected.arrayed_toggle ? (
              <div style={FIELD}>
                <div style={LABEL}>
                  <span>批处理大小</span>
                  <span style={TAG}>structural</span>
                </div>
                <input
                  type="number"
                  min={1}
                  step={1}
                  data-hl-batch-size=""
                  value={Math.max(1, Math.round(Number(selected.batch_size ?? 1)))}
                  onChange={(e) => {
                    const raw = Number(e.target.value);
                    const clamped = Number.isFinite(raw)
                      ? Math.max(1, Math.round(raw))
                      : 1;
                    onChange({ batch_size: clamped });
                  }}
                  style={{
                    width: 72,
                    fontFamily: "var(--font-mono)",
                    fontVariantNumeric: "tabular-nums",
                    textAlign: "left",
                  }}
                />
                <details className="hl-field-help"><summary>Help</summary><div style={{ ...HINT, marginTop: 4 }}>
                  每个 shard 子进程处理的数组元素数（默认 1 = 每元素一个 job）。
                  <code>N</code> 元素 + <code>batch_size=B</code> ⇒ <code>ceil(N/B)</code> 个 shard；
                  批内任一元素失败即整批失败（<code>set -euo pipefail</code>）。
                  改动会 Fork 新的快照。
                </div></details>
              </div>
            ) : null}
          </>
        )}

        <h3 style={{ ...SECTION_TITLE, marginTop: 18 }}>Parameters</h3>
        {Object.keys(pack.params).length === 0 ? (
          <div style={{ ...HINT, color: "var(--text-subtle)" }}>
            This pack takes no parameters.
          </div>
        ) : (
          Object.entries(pack.params).map(([name, spec]) => {
            if (spec.type === "path") {
              return <div key={name} style={FIELD}>
                <div style={LABEL}><code>{name}</code><span style={TAG}>path</span></div>
                <PathInput key={`${selected.id}:${name}:${selected.assigned_node_id}`} name={name}
                  value={String(selected.params[name] ?? spec.default ?? "")}
                  nodeId={selected.assigned_node_id ?? null} pathKind={spec.path_kind}
                  initialDirectory={computeNodesById?.[selected.assigned_node_id ?? ""]?.workspace_root ?? "/"}
                  onChange={value => onChange({ params: { ...selected.params, [name]: value } })} />
                {spec.description && <div style={HINT}>{spec.description}</div>}
              </div>;
            }
            // list[str] gets the DimListEditor: pack authors mark ordered-
            // label params (regroup's ``input_dims`` / ``output_dims``)
            // with this type so the inspector can render a proper editor
            // instead of a comma-separated string blob in a text input.
            // Passive permutation hint when ``output_dims`` doesn't match
            // ``input_dims`` — the backend enforces, this is just early
            // signal for the operator.
            if (spec.type === "list[str]" || spec.type === "list<str>") {
              const raw =
                (selected.params[name] as unknown) ??
                (spec.default as unknown) ??
                [];
              const arr = Array.isArray(raw)
                ? raw.map((v) => String(v ?? ""))
                : [];
              const otherName =
                name === "output_dims"
                  ? "input_dims"
                  : name === "input_dims"
                    ? "output_dims"
                    : null;
              // Only apply the permutation-hint when this looks like the
              // regroup pattern (both params exist AND we're editing
              // output_dims) — comparing input_dims to output_dims in
              // both directions would double-warn.
              const matchAgainst =
                otherName === "input_dims" && pack.params[otherName]
                  ? (
                      (selected.params[otherName] as unknown) ??
                      (pack.params[otherName].default as unknown) ??
                      []
                    )
                  : undefined;
              const matchArr =
                Array.isArray(matchAgainst)
                  ? matchAgainst.map((v) => String(v ?? ""))
                  : undefined;
              return (
                <div key={name} className="hl-inspector-field" style={FIELD}>
                  <div style={LABEL}>
                    <code style={{ fontFamily: "var(--font-mono)" }}>{name}</code>
                    <span style={TAG}>{spec.type}</span>
                    {spec.optional ? (
                      <span style={{ ...TAG, color: "var(--text-subtle)" }}>optional</span>
                    ) : null}
                  </div>
                  <DimListEditor
                    value={arr}
                    onChange={(next) =>
                      onChange({
                        params: { ...selected.params, [name]: next },
                      })
                    }
                    matchAgainst={matchArr}
                    ariaName={name}
                    placeholder="dim label"
                  />
                  {spec.description && <details className="hl-field-help"><summary>Help</summary><div style={HINT}>{spec.description}</div></details>}
                </div>
              );
            }
            const current =
              (selected.params[name] as string | number | boolean | undefined) ??
              (spec.default as string | number | boolean | undefined) ??
              "";
            const isNumeric = spec.type === "int" || spec.type === "float";
            return (
              <div key={name} className="hl-inspector-field" style={FIELD}>
                <div style={LABEL}>
                  <code style={{ fontFamily: "var(--font-mono)" }}>{name}</code>
                  <span style={TAG}>{spec.type}</span>
                  {spec.optional ? (
                    <span style={{ ...TAG, color: "var(--text-subtle)" }}>optional</span>
                  ) : null}
                </div>
                <input
                  aria-label={name}
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
                    maxWidth: isNumeric ? 160 : 260,
                    fontFamily: isNumeric ? "var(--font-mono)" : "inherit",
                    fontVariantNumeric: isNumeric ? "tabular-nums" : "normal",
                    textAlign: "left",
                  }}
                />
                {spec.description && <details className="hl-field-help"><summary>Help</summary><div style={HINT}>{spec.description}</div></details>}
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
