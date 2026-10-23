import type { CatalogPack } from "../wire";
import { ArrayParameterInput, ParameterInput, Vec3ArrayParameterInput } from "./ParameterInput";
import { DimListEditor } from "./DimListEditor";
import { arrayParamsForEditor } from "./arrayMigration";

export function packParameterRows(pack: CatalogPack, params: Record<string, unknown>) {
  const editable = arrayParamsForEditor(pack.name, pack.version, params);
  const keys = ["x", "y", "scale"];
  const columns = keys.map(key => editable[key] ?? pack.params[key]?.default ?? []);
  const grouped = ((pack.name === "vec3-array-source" && pack.version === "0.1.0") || pack.name === "@builtin/constant-vec3-array")
    && keys.every(key => pack.params[key]?.type === "array" && pack.params[key]?.items === "float")
    && columns.every(Array.isArray)
    && new Set(columns.map(value => (value as unknown[]).length)).size === 1;
  return Object.entries(pack.params).flatMap(([name, spec]) => {
    if (grouped && (name === "y" || name === "scale")) return [];
    return [{ name, spec, names: grouped && name === "x" ? keys : [name],
      label: grouped && name === "x" ? "transform" : name,
      type: grouped && name === "x" ? "x / y / scale" : spec.type === "array" ? `array<${spec.items}>` : spec.type }];
  });
}

/** The value presented by the existing editor, not execution-time resolution. */
export function parameterEditorValue(pack: CatalogPack, params: Record<string, unknown>, name: string, grouped = false): unknown {
  const editable = arrayParamsForEditor(pack.name, pack.version, params);
  const spec = pack.params[name];
  if (grouped || spec.type === "list[str]" || spec.type === "list<str>") {
    return editable[name] ?? spec.default ?? [];
  }
  return Object.prototype.hasOwnProperty.call(editable, name)
    ? editable[name] : spec.default ?? (spec.type === "array" ? [] : "");
}

/** One dispatch path for Inspector and canvas; callbacks contain only changed fields. */
export function PackParameterField({ pack, params, name, names, nodeId, initialDirectory, onChange }: {
  pack: CatalogPack; params: Record<string, unknown>; name: string; names: string[];
  nodeId: string | null; initialDirectory: string;
  onChange: (patch: Record<string, unknown>) => void;
}) {
  const editable = arrayParamsForEditor(pack.name, pack.version, params);
  const spec = pack.params[name];
  if (names.length > 1) return <Vec3ArrayParameterInput specs={pack.params} params={editable}
    nodeId={nodeId} initialDirectory={initialDirectory} onChange={onChange} />;
  if (spec.type === "list[str]" || spec.type === "list<str>") {
    const raw = parameterEditorValue(pack, params, name);
    const other = name === "output_dims" && pack.params.input_dims
      ? editable.input_dims ?? pack.params.input_dims.default ?? [] : undefined;
    return <DimListEditor value={Array.isArray(raw) ? raw.map(v => String(v ?? "")) : []}
      matchAgainst={Array.isArray(other) ? other.map(v => String(v ?? "")) : undefined}
      ariaName={name} placeholder="dim label" onChange={value => onChange({ [name]: value })} />;
  }
  const Input = spec.type === "array" ? ArrayParameterInput : ParameterInput;
  const value = parameterEditorValue(pack, params, name);
  return <Input name={name} spec={spec} value={value} nodeId={nodeId} initialDirectory={initialDirectory}
    onChange={next => onChange({ [name]: next })} />;
}

/** Merge against the latest state, including legacy array read-time conversion. */
export function mergeParameterPatch(pack: Pick<CatalogPack, "name" | "version">,
  current: Record<string, unknown>, patch: Record<string, unknown>) {
  return { ...arrayParamsForEditor(pack.name, pack.version, current), ...patch };
}
