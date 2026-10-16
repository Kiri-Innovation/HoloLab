// Compact right-sidebar parameter editor. Changes use the existing draft autosave.

import { useId, useState, useRef, useEffect, useLayoutEffect, type ReactNode, type CSSProperties } from "react";
import { createPortal } from "react-dom";
import type { CatalogPack, GraphNode } from "../wire";
import { MarkdownView } from "../ui/MarkdownView";
import { PathInput } from "./PathInput";
import type { ComputeNode } from "../wire";
import { DimListEditor } from "./DimListEditor";

export interface NodeInspectorProps {
  selected: GraphNode | null;
  pack: CatalogPack | null;
  computeNodesById?: Record<string, ComputeNode>;
  onChange: (patch: Partial<GraphNode>) => void;
  assignment?: ReactNode;
}

const SECTION_TITLE: CSSProperties = {
  fontSize: 10,
  fontWeight: 600,
  letterSpacing: "0.08em",
  textTransform: "uppercase",
  color: "var(--text-subtle)",
  margin: "0 0 10px",
};

const FIELD: CSSProperties = { marginBottom: 6 };

const LABEL: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
  fontSize: "var(--fs-xs)",
  fontWeight: 400,
  color: "var(--text-muted)",
  marginBottom: 0,
};

const HINT: CSSProperties = {
  fontSize: "var(--fs-xs)",
  color: "var(--text-muted)",
  marginTop: 4,
  lineHeight: 1.4,
};

export function NodeInspector({
  selected,
  pack,
  computeNodesById,
  onChange,
  assignment,
}: NodeInspectorProps) {

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

  return (
    <div
      className="hl-node-inspector"
      style={{
        padding: "14px",
        height: "100%",
        fontSize: "var(--fs-sm)",
        color: "var(--text-body)",
        display: "flex",
        flexDirection: "column",
        boxSizing: "border-box",
        overflow: "hidden",
        minHeight: 0,
      }}
    >
      <section className="hl-inspector-summary" aria-label="Node overview">
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
          <PackDescription key={`${selected.id}:${pack.name}:${pack.version}`} pack={pack} />
        </div>

      </section>
      {assignment && <section className="hl-inspector-execution" aria-label="Run on">
        <h3 style={SECTION_TITLE}>Run on</h3>
        {assignment}
      </section>}
      <div className="hl-inspector-parameters" role="region" aria-label="Parameters">
        <h3 style={{ ...SECTION_TITLE, marginTop: 18 }}>Parameters</h3>
        {pack.arrayable && (
          <>
            <h3 style={{ ...SECTION_TITLE, marginTop: 18 }}>Fan-out</h3>
            <div className="hl-inspector-field" style={FIELD}>
              <label className="hl-inspector-toggle-label">
                <span>并行处理数组输入 <span className="hl-parameter-type">bool</span></span>
                <input
                  type="checkbox"
                  data-hl-arrayed-toggle=""
                  checked={Boolean(selected.arrayed_toggle)}
                  onChange={(e) => onChange({ arrayed_toggle: e.target.checked })}
                  style={{ marginTop: 2 }}
                />
              </label>
                  <ParameterHelp name="Fan-out">
                    勾选后此节点的输入/输出端口变为 <code>arrayed&lt;T&gt;</code>；
                    运行时框架对每个数组元素起一个 sub-job，
                    产物聚合在 <code>{"{parent}/{port}/{element_id}/"}</code>。
                  </ParameterHelp>
            </div>
            {selected.arrayed_toggle ? (
              <div className="hl-inspector-field hl-inspector-field--nested" style={FIELD}>
                <div style={LABEL}>
                  <span>并行执行数</span><span className="hl-parameter-type">int</span>
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
                    width: "100%",
                    fontFamily: "var(--font-mono)",
                    fontVariantNumeric: "tabular-nums",
                    textAlign: "left",
                  }}
                />
                <ParameterHelp name="parallelism">
                  同时派发的 shard 上限（1–8，默认 1 = 串行）。
                  实际并发 = min(本值, 节点守护进程 <code>max_concurrent_jobs</code>)。
                  改动会 Fork 新的快照。
                </ParameterHelp>
              </div>
            ) : null}
            {selected.arrayed_toggle ? (
              <div className="hl-inspector-field hl-inspector-field--nested" style={FIELD}>
                <div style={LABEL}>
                  <span>批处理大小</span><span className="hl-parameter-type">int</span>
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
                    width: "100%",
                    fontFamily: "var(--font-mono)",
                    fontVariantNumeric: "tabular-nums",
                    textAlign: "left",
                  }}
                />
                <ParameterHelp name="batch_size">
                  每个 shard 子进程处理的数组元素数（默认 1 = 每元素一个 job）。
                  <code>N</code> 元素 + <code>batch_size=B</code> ⇒ <code>ceil(N/B)</code> 个 shard；
                  批内任一元素失败即整批失败（<code>set -euo pipefail</code>）。
                  改动会 Fork 新的快照。
                </ParameterHelp>
              </div>
            ) : null}
          </>
        )}

        {Object.keys(pack.params).length === 0 ? (
          <div style={{ ...HINT, color: "var(--text-subtle)" }}>
            This pack takes no parameters.
          </div>
        ) : (
          Object.entries(pack.params).map(([name, spec]) => {
            if (spec.type === "path") {
              return <div key={name} className="hl-inspector-field" style={FIELD}>
                <ParameterLabel name={name} type={spec.type} optional={spec.optional} />
                <PathInput key={`${selected.id}:${name}:${selected.assigned_node_id}`} name={name}
                  value={String(selected.params[name] ?? spec.default ?? "")}
                  nodeId={selected.assigned_node_id ?? null} pathKind={spec.path_kind}
                  initialDirectory={computeNodesById?.[selected.assigned_node_id ?? ""]?.workspace_root ?? "/"}
                  onChange={value => onChange({ params: { ...selected.params, [name]: value } })} />
                {spec.description && <ParameterHelp key={`${selected.id}:${name}`} name={name}>{spec.description}</ParameterHelp>}
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
                  <ParameterLabel name={name} type={spec.type} optional={spec.optional} />
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
                  {spec.description && <ParameterHelp key={`${selected.id}:${name}`} name={name}>{spec.description}</ParameterHelp>}
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
                <ParameterLabel name={name} type={spec.type} optional={spec.optional} />
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
                    maxWidth: "100%",
                    fontFamily: isNumeric ? "var(--font-mono)" : "inherit",
                    fontVariantNumeric: isNumeric ? "tabular-nums" : "normal",
                    textAlign: "left",
                  }}
                />
                {spec.description && <ParameterHelp key={`${selected.id}:${name}`} name={name}>{spec.description}</ParameterHelp>}
              </div>
            );
          })
        )}
      </div>


    </div>
  );
}

function ParameterLabel({ name, type, optional }: { name: string; type: string; optional?: boolean }) {
  return <div style={LABEL} title={`${name}${optional ? " · optional" : ""}`}>
    <code>{name}</code><span className="hl-parameter-type" title={type}>{type}</span>
  </div>;
}

function ParameterHelp({ name, children }: { name: string; children: ReactNode }) {
  const id = useId();
  const anchor = useRef<HTMLButtonElement>(null);
  const card = useRef<HTMLDivElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState<CSSProperties>({ visibility: "hidden" });
  const cancelTimer = () => clearTimeout(timer.current);
  const close = () => { cancelTimer(); setOpen(false); };

  useEffect(() => () => clearTimeout(timer.current), []);
  useLayoutEffect(() => {
    if (!open || !anchor.current || !card.current) return;
    const place = () => {
      if (!anchor.current || !card.current) return;
      const row = anchor.current.closest(".hl-inspector-field")!.getBoundingClientRect();
      const visibleArea = anchor.current.closest(".hl-inspector-parameters")!.getBoundingClientRect();
      if (!row.width || row.bottom < Math.max(0, visibleArea.top) || row.top > Math.min(window.innerHeight, visibleArea.bottom)) {
        setOpen(false);
        return;
      }
      const panel = anchor.current.closest(".hl-node-inspector")!.getBoundingClientRect();
      const bubble = card.current.getBoundingClientRect();
      const edge = 8;
      const viewportWidth = document.documentElement.clientWidth;
      const viewportHeight = window.innerHeight;
      let left = panel.left - bubble.width - edge;
      let top = row.top;
      if (left < edge) {
        left = panel.right + edge;
        if (left + bubble.width > viewportWidth - edge) {
          left = Math.max(edge, Math.min(row.left, viewportWidth - bubble.width - edge));
          top = row.bottom + edge;
        }
      }
      top = Math.max(edge, Math.min(top, viewportHeight - bubble.height - edge));
      setPosition({ left, top, visibility: "visible" });
    };
    place();
    const dismiss = () => { clearTimeout(timer.current); setOpen(false); };
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") dismiss(); };
    window.addEventListener("keydown", onKey);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open, children]);

  return <>
    <button ref={anchor} type="button" className="hl-parameter-help"
      aria-label={`Help for ${name}`} aria-describedby={open ? id : undefined}
      onPointerEnter={() => { cancelTimer(); timer.current = setTimeout(() => setOpen(true), 150); }}
      onPointerLeave={close}
      onFocus={() => { cancelTimer(); setOpen(true); }} onBlur={close}
    >?</button>
    {open && createPortal(<div ref={card} id={id} role="tooltip"
      className="hl-parameter-tooltip" style={position}>{children}</div>, document.body)}
  </>;
}

function PackDescription({ pack }: { pack: CatalogPack }) {
  const [expanded, setExpanded] = useState(false);
  const docsId = useId();
  const hasDocs = !!pack.docs?.trim();

  return <>
    {(pack.description?.trim() || hasDocs) && (
      <p className="hl-inspector-description">
        {pack.description}
        {hasDocs && <>{pack.description?.trim() ? " " : null}<button
          type="button"
          className="hl-inspector-docs-toggle"
          aria-expanded={expanded}
          aria-controls={docsId}
          onClick={() => setExpanded(value => !value)}
        >{expanded ? "Show Less" : "Read More"}</button></>}
      </p>
    )}
    {hasDocs && <div id={docsId} className="hl-inspector-docs" hidden={!expanded}
      role="region" aria-label="Pack documentation" tabIndex={0}>
      <MarkdownView source={pack.docs!} />
    </div>}
  </>;
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
