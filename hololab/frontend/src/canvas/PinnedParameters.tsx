import { useLayoutEffect, useRef } from "react";
import { useUpdateNodeInternals } from "@xyflow/react";
import type { AlgorithmNodeData } from "./AlgorithmNode";
import { useCanvasContext } from "./CanvasContext";
import { PackParameterField, packParameterRows } from "./PackParameterField";
import { effectiveParameterPins } from "./parameterPins";
import { arrayParamsForEditor } from "./arrayMigration";

/** Presentation only: uses the same fields and update path as Inspector. */
export function PinnedParameters({ id, data }: { id: string; data: AlgorithmNodeData }) {
  const context = useCanvasContext();
  const pins = data.readOnly ? data.pinnedParams ?? [] : effectiveParameterPins(context.pinnedParams ?? {}, data.pack);
  const params = data.params ?? {};
  const rows = packParameterRows(data.pack, params).filter(row => row.names.some(name => pins.includes(name)));
  const signature = rows.map(row => row.name).join("\0");
  const root = useRef<HTMLDivElement>(null);
  const update = useUpdateNodeInternals();
  useLayoutEffect(() => {
    let frame = 0;
    const measure = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(() => update(id)); };
    measure();
    const observer = new ResizeObserver(measure);
    if (root.current) observer.observe(root.current);
    return () => { observer.disconnect(); cancelAnimationFrame(frame); };
  }, [id, signature, update]);
  if (!rows.length) return null;
  const editable = arrayParamsForEditor(data.pack.name, data.pack.version, params);
  return <div ref={root} className="hl-pinned-parameters hl-inspector-parameters nodrag nopan nowheel"
    role="group" aria-label="Pinned parameters" onKeyDown={event => event.stopPropagation()}
    onPointerDown={event => event.stopPropagation()} onDoubleClick={event => event.stopPropagation()}>
    {rows.map(({ name, names, label, type }) => {
      const field = <PackParameterField key={`${id}:${name}:${data.assigned_node_id}`} pack={data.pack} params={params}
        name={name} names={names} nodeId={data.assigned_node_id}
        initialDirectory={context.computeNodesById[data.assigned_node_id ?? ""]?.workspace_root ?? "/"}
        variables={context.variables} binding={data.param_bindings?.[name]}
        effectiveValue={context.effectiveParamsByNode?.[id]?.[name]}
        onBindingChange={context.onBindingChange ? variable => context.onBindingChange?.(id, name, variable) : undefined}
        onChange={patch => context.onParamsChange?.(id, patch)} />;
      return <div className="hl-inspector-field" key={name}>
      <div className="hl-pinned-label" title={`${label} · ${type}`}><code>{label}</code><span className="hl-parameter-type">{type}</span></div>
      {data.readOnly ? <div className="hl-pinned-readonly" aria-label={`${label} (read only)`}>
        {names.map(key => {
          const value = Object.prototype.hasOwnProperty.call(editable, key) ? editable[key] : data.pack.params[key]?.default;
          return <div key={key}>{names.length > 1 ? `${key}: ` : ""}{value === undefined ? "—" : typeof value === "string" ? value : JSON.stringify(value)}</div>;
        })}
      </div> : type === "bool" ? <label className="hl-pinned-checkbox">{field}</label> : field}
    </div>; })}
  </div>;
}
