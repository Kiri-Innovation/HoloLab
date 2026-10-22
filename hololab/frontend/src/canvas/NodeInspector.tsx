import { InlineInputEditor } from "./InlineInputEditor";
import { isConstantName } from "./constants";
// Compact right-sidebar parameter editor. Changes use the existing draft autosave.

import { useId, useState, useRef, useEffect, useLayoutEffect, type ReactNode, type CSSProperties } from "react";
import { createPortal } from "react-dom";
import type { CatalogPack, GraphNode } from "../wire";
import { MarkdownView } from "../ui/MarkdownView";
import { PackParameterField, packParameterRows } from "./PackParameterField";
import type { ComputeNode } from "../wire";
import { ParameterPinButton } from "./ParameterPinButton";
import { parameterPinKey } from "./parameterPins";
import { OpenSourceButton } from "./OpenSourceButton";

export interface NodeInspectorProps {
  selected: GraphNode | null;
  inputSources?: Record<string,string>;
  pack: CatalogPack | null;
  computeNodesById?: Record<string, ComputeNode>;
  onChange: (patch: Partial<GraphNode>) => void;
  onParamsChange: (patch: Record<string, unknown>) => void;
  assignment?: ReactNode;
  preview?: ReactNode;
  pinnedParams?: string[];
  onTogglePin?: (names: string[]) => void;
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
  inputSources,
  pack,
  computeNodesById,
  onChange,
  onParamsChange,
  assignment,
  preview,
  pinnedParams = [],
  onTogglePin,
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

  const pin = (names: string[]) => onTogglePin ? <ParameterPinButton names={names}
    algorithmKey={parameterPinKey(pack)} pinned={pinnedParams} onToggle={onTogglePin} /> : null;
  const packageComputeNode =
    (selected.assigned_node_id ? computeNodesById?.[selected.assigned_node_id] : null)
    ?? (pack.node_ids ?? []).map((nodeId) => computeNodesById?.[nodeId]).find(Boolean)
    ?? null;

  return (
    <div
      className="hl-node-inspector"
      style={{
        padding: "14px",
        height: "auto",
        fontSize: "var(--fs-sm)",
        color: "var(--text-body)",
        display: "flex",
        flexDirection: "column",
        boxSizing: "border-box",
        overflow: "visible",
        minHeight: "100%",
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
      {!isConstantName(pack.name) && assignment && <section className="hl-inspector-execution" aria-label="Run on">
        <h3 style={SECTION_TITLE}>Run on</h3>
        {assignment}
      </section>}
      {Object.keys(pack.inputs).length > 0 && <section className="hl-inspector-inputs hl-inspector-parameters" aria-label="Input values">
        <h3 style={{ ...SECTION_TITLE, marginTop: 18 }}>Input values</h3>
        <InlineInputEditor node={selected} pack={pack} sources={inputSources} onChange={port_values=>onChange({port_values})} />
      </section>}
      <div className="hl-inspector-parameters" role="region" aria-label="Parameters">
        <h3 style={{ ...SECTION_TITLE, marginTop: 18 }}>Parameters</h3>
        {!isConstantName(pack.name) && <div className="hl-inspector-field hl-inspector-package-field" style={FIELD}>
          <ParameterLabel name="package" type="path" />
          <OpenSourceButton
            pack={pack}
            computeNode={packageComputeNode}
            variant="row"
            label="manifest.yaml"
          />
        </div>}
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
          packParameterRows(pack, selected.params).map(({ name, spec, names, label, type }) =>
            <div key={name} className={`hl-inspector-field${onTogglePin ? " hl-inspector-field--pinnable" : ""}`} style={FIELD}>
              <ParameterLabel name={label} type={type} optional={spec.optional} />
              <PackParameterField key={`${selected.id}:${name}:${selected.assigned_node_id}`}
                pack={pack} params={selected.params} name={name} names={names}
                nodeId={selected.assigned_node_id ?? null}
                initialDirectory={computeNodesById?.[selected.assigned_node_id ?? ""]?.workspace_root ?? "/"}
                onChange={onParamsChange} />
              {pin(names)}
              {spec.description && <ParameterHelp key={`${selected.id}:${name}`} name={name}>{spec.description}</ParameterHelp>}
            </div>
          )
        )}
      </div>
      {preview}
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
  const [arrowSide, setArrowSide] = useState<"left" | "right">("left");
  const [position, setPosition] = useState<CSSProperties>({ visibility: "hidden" });
  const cancelTimer = () => clearTimeout(timer.current);
  const close = () => { cancelTimer(); setOpen(false); };

  useEffect(() => () => clearTimeout(timer.current), []);
  useLayoutEffect(() => {
    if (!open || !anchor.current || !card.current) return;
    const place = () => {
      if (!anchor.current || !card.current) return;
      const row = anchor.current.closest(".hl-inspector-field")!.getBoundingClientRect();
      const visibleArea = anchor.current.closest(".hl-sidebar-content, .hl-mobile-inspector")!.getBoundingClientRect();
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
      let side: "left" | "right" = "left";
      if (left < edge) {
        left = panel.right + edge;
        side = "right";
        if (left + bubble.width > viewportWidth - edge) {
          left = Math.max(edge, Math.min(row.left, viewportWidth - bubble.width - edge));
          top = row.bottom + edge;
          side = "left";
        }
      }
      top = Math.max(edge, Math.min(top, viewportHeight - bubble.height - edge));
      const trigger = anchor.current.getBoundingClientRect();
      const arrowY = Math.max(8, Math.min(trigger.top + trigger.height / 2 - top - 1, bubble.height - 10));
      setArrowSide(side === "left" ? "right" : "left");
      setPosition({ left, top, visibility: "visible", "--parameter-arrow-y": `${arrowY}px` } as CSSProperties);
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
      className="hl-parameter-tooltip" data-arrow-side={arrowSide} style={position}>
      <span className="hl-parameter-tooltip-arrow" aria-hidden="true" />
      <div className="hl-parameter-tooltip-content">{children}</div>
    </div>, document.body)}
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
