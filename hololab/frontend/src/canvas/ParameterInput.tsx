import type { ParamSpec } from "../wire";
import { EnumSelect } from "./EnumSelect";
import { PathInput } from "./PathInput";

export interface ParameterInputProps {
  name: string;
  spec: ParamSpec;
  value: unknown;
  nodeId: string | null;
  initialDirectory: string;
  onChange: (value: unknown) => void;
}

/** Shared scalar dispatch for standalone parameters and array rows. */
export function ParameterInput({ name, spec, value, nodeId, initialDirectory, onChange }: ParameterInputProps) {
  if (spec.type === "path") return <PathInput name={name} value={String(value ?? "")}
    nodeId={nodeId} pathKind={spec.path_kind} initialDirectory={initialDirectory} onChange={onChange} />;
  if (spec.type === "enum") {
    const values = spec.values ?? [];
    const index = values.findIndex(option => option === value);
    return <>
      {index >= 0 ? <EnumSelect name={name} values={values} index={index} onChange={onChange} />
        : <input aria-label={name} value={String(value ?? "")} onChange={e => onChange(e.target.value)} />}
      {index < 0 && <small style={{ color: "var(--text-muted)" }}>
        {values.length ? "Current value is not in the available options." : "No enum options are declared; the current value is preserved."}
      </small>}
    </>;
  }
  if (spec.type === "bool") return <input aria-label={name} type="checkbox" checked={value === true}
    onChange={e => onChange(e.target.checked)} />;
  const numeric = spec.type === "int" || spec.type === "float";
  return <input aria-label={name} type={numeric ? "number" : "text"}
    min={spec.min ?? undefined} max={spec.max ?? undefined} step={spec.type === "int" ? 1 : "any"}
    value={String(value ?? "")} onChange={e => {
      const raw = e.target.value;
      const number = Number(raw);
      onChange(numeric && raw.trim() && Number.isFinite(number) ? number : raw);
    }} style={{ width: "100%", minWidth: 0, maxWidth: "100%", fontFamily: numeric ? "var(--font-mono)" : "inherit" }} />;
}

function newElement(spec: ParamSpec): unknown {
  if (spec.type === "bool") return false;
  if (spec.type === "enum") return spec.values?.[0] ?? "";
  if (spec.type === "int" || spec.type === "float") return Math.max(spec.min ?? -Infinity, Math.min(spec.max ?? Infinity, 0));
  return "";
}

export function ArrayParameterInput(props: ParameterInputProps) {
  const { name, spec, value, onChange } = props;
  const element = { ...spec, type: spec.items ?? "string", items: undefined };
  // Never silently discard an old/invalid persisted value on render.
  if (!Array.isArray(value)) return <div role="alert">
    <small>Saved value is not an array. It is preserved until replaced.</small>
    <button type="button" onClick={() => onChange([])}>Replace with empty array</button>
  </div>;
  return <div className="hl-array-parameter" role="group" aria-label={`${name} array`}
    style={{ gridColumn: "1 / -1", minWidth: 0, display: "grid", gap: 5 }}>
    {value.map((item, index) => <div key={index} className="hl-array-parameter-row"
      style={{ display: "grid", gridTemplateColumns: "24px minmax(0, 1fr) 26px", alignItems: "center", gap: 5 }}>
      <span style={{ color: "var(--text-muted)", fontSize: "var(--fs-xs)" }}>{index}</span>
      <div style={{ minWidth: 0 }}><ParameterInput {...props} name={`${name}[${index}]`} spec={element} value={item}
        onChange={next => onChange(value.map((old, i) => i === index ? next : old))} /></div>
      <button type="button" aria-label={`Remove ${name}[${index}]`}
        onClick={() => onChange(value.filter((_, i) => i !== index))}>−</button>
    </div>)}
    <button type="button" aria-label={`Add ${name} item`} style={{ justifySelf: "start" }}
      onClick={() => onChange([...value, newElement(element)])}>+ Add item</button>
  </div>;
}

/** Presentation adapter only: three scalar arrays remain independent in the IR. */
export function Vec3ArrayParameterInput({ specs, params, onChange, ...context }: {
  specs: Record<string, ParamSpec>;
  params: Record<string, unknown>;
  onChange: (params: Record<string, unknown>) => void;
  nodeId: string | null;
  initialDirectory: string;
}) {
  const keys = ["x", "y", "scale"];
  const columns = keys.map(key => (params[key] ?? specs[key].default ?? []) as unknown[]);
  const update = (next: unknown[][]) => onChange(Object.fromEntries(keys.map((key, i) => [key, next[i]])));
  return <div role="group" aria-label="transform array" style={{ gridColumn: "1 / -1", display: "grid", gap: 5, minWidth: 0 }}>
    <div style={{ display: "grid", gridTemplateColumns: "24px repeat(3, minmax(0, 1fr)) 26px", gap: 5, color: "var(--text-muted)" }}>
      <span />{keys.map(key => <small key={key}>{key}</small>)}<span />
    </div>
    {columns[0].map((_, index) => <div key={index} style={{ display: "grid", gridTemplateColumns: "24px repeat(3, minmax(0, 1fr)) 26px", gap: 5, alignItems: "center" }}>
      <span style={{ color: "var(--text-muted)" }}>{index}</span>
      {keys.map((key, column) => <ParameterInput key={key} {...context} name={`${key}[${index}]`}
        spec={{ ...specs[key], type: "float" }} value={columns[column][index]}
        onChange={value => update(columns.map((items, c) => c === column ? items.map((old, i) => i === index ? value : old) : items))} />)}
      <button type="button" aria-label={`Remove transform[${index}]`} onClick={() => update(columns.map(items => items.filter((_, i) => i !== index)))}>−</button>
    </div>)}
    <button type="button" aria-label="Add transform item" style={{ justifySelf: "start" }}
      onClick={() => update(columns.map((items, i) => [...items, i === 2 ? 1 : 0]))}>+ Add item</button>
  </div>;
}
