import "./workflowEvidence.css";
import { canonicalPackageName } from "../packageIdentity";
import { parameterDifferenceText } from "./parameterDifferences";
import type { VariableDeclarations } from "../wire";
import type { CatalogPack } from "../wire";
import { ArrayParameterInput, ParameterInput, Vec3ArrayParameterInput } from "./ParameterInput";
import { DimListEditor } from "./DimListEditor";
import { arrayParamsForEditor } from "./arrayMigration";

export function packParameterRows(pack: CatalogPack, params: Record<string, unknown>) {
  const editable = arrayParamsForEditor(pack.name, pack.version, params);
  const keys = ["x", "y", "scale"];
  const columns = keys.map(key => editable[key] ?? pack.params[key]?.default ?? []);
  const grouped = ((canonicalPackageName(pack.name) === "kiri/vec3-array-source" && pack.version === "0.1.0") || pack.name === "@builtin/constant-vec3-array")
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
function InlinePackParameterField({ pack, params, name, names, nodeId, initialDirectory, onChange }: {
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


export function matchingVariables(variables: VariableDeclarations, type: string) {
  return Object.entries(variables).filter(([, declaration]) => declaration.type === type).map(([name]) => name);
}

/** Binding changes never overwrite params: switching back restores the inline editor. */
export function PackParameterField(props: Parameters<typeof InlinePackParameterField>[0] & {
  variables?: VariableDeclarations; binding?: string; effectiveValue?: unknown;
  onBindingChange?: (variable: string | null) => void;
}) {
  const { variables = {}, binding, onBindingChange, effectiveValue } = props;
  const type = props.pack.params[props.name].type;
  if (!onBindingChange || (type !== "path" && type !== "int") || props.names.length !== 1) {
    return InlinePackParameterField(props);
  }
  const choices = matchingVariables(variables, type);
  const bound = binding !== undefined;
  const text = parameterDifferenceText({name: props.name, displayed: `→ ${binding ?? ""}`,
    effective: effectiveValue, reason: "workflow-variable"});
  return <div className="hl-variable-parameter">
    <div className="hl-variable-parameter-control">
      <button type="button" className="hl-variable-mode-toggle"
        title={bound ? "当前为绑定变量；点击切换为内联值" : "当前为内联值；点击切换为绑定变量"}
        aria-label={`${props.name}：${bound ? "绑定变量" : "内联值"}；点击切换`}
        aria-expanded={bound}
        onClick={() => onBindingChange(bound ? null : choices[0] ?? "")}>
        {bound ? <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6 5 3 8l3 3M10 5l3 3-3 3M3 8h10" /></svg>
          : <svg viewBox="0 0 16 16" aria-hidden="true"><path d="m6 3-3 5 3 5M10 3l3 5-3 5M4 8h8" /></svg>}
      </button>
      {bound ? <div title={text}>
        <select aria-label={`${props.name} 绑定变量`} value={binding}
          onChange={event => onBindingChange(event.target.value)}>
          <option value="">选择变量</option>
          {binding && !choices.includes(binding) && <option value={binding}>→ {binding}（缺失或类型不匹配）</option>}
          {choices.map(variable => <option key={variable} value={variable}>→ {variable}</option>)}
        </select>
        {!choices.includes(binding ?? "") && <p role="status">{binding ? "声明缺失或类型不匹配；请重新选择。" : "尚未选择变量。"}</p>}
        {!choices.length && <p>请在工作流变量中添加 {type === "int" ? "整数" : "路径"} 变量。</p>}
        <details><summary>解析值{effectiveValue === undefined ? " · 尚未解析" : ""}</summary>{text}</details>
      </div> : <InlinePackParameterField {...props} />}
    </div>
  </div>;
}
