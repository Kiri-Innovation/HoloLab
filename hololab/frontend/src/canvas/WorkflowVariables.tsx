import { useState } from "react";
import type { VariableDeclarations } from "../wire";

export function WorkflowVariables({ variables, values, onChange }: {
  variables: VariableDeclarations; values: Record<string, unknown>;
  onChange: (variables: VariableDeclarations, values: Record<string, unknown>) => void;
}) {
  const [name, setName] = useState("");
  const [type, setType] = useState<"path" | "int">("path");
  const [error, setError] = useState("");
  return <details style={{ padding: 12 }}><summary>工作流变量（{Object.keys(variables).length}）</summary>
    <p>声明与默认值随配方共享；本地值只用于本工作流。参数绑定后由统一解析器取值。</p>
    {Object.entries(variables).map(([key, declaration]) => <fieldset key={key}>
      <legend>{key} · {declaration.type}</legend>
      <label>说明<input aria-label={`${key} 说明`} value={declaration.description ?? ""}
        onChange={e => onChange({...variables, [key]: {...declaration, description: e.target.value}}, values)} /></label>
      <label>共享默认值<input aria-label={`${key} 默认值`} type={declaration.type === "int" ? "number" : "text"} step="1"
        value={String(declaration.default ?? "")} onChange={e => onChange({...variables,
          [key]: {...declaration, default: declaration.type === "int" ? (e.target.value === "" ? null : e.target.valueAsNumber) : e.target.value}}, values)} /></label>
      <label>本地值<input aria-label={`${key} 本地值`} type={declaration.type === "int" ? "number" : "text"} step="1"
        placeholder="未提供本地值；使用默认值" value={String(values[key] ?? "")}
        onChange={e => onChange(variables, {...values, [key]: declaration.type === "int"
          ? (e.target.value === "" ? null : e.target.valueAsNumber) : e.target.value})} /></label>
      <button onClick={() => { const next = {...values}; delete next[key]; onChange(variables, next); }}>使用默认值</button>
      <button onClick={() => { const next = {...variables}, local = {...values}; delete next[key]; delete local[key]; onChange(next, local); }}>删除声明</button>
    </fieldset>)}
    <input aria-label="新变量名" placeholder="变量名" value={name} onChange={e => setName(e.target.value)} />
    <select aria-label="新变量类型" value={type} onChange={e => setType(e.target.value as "path" | "int")}>
      <option value="path">path</option><option value="int">int</option>
    </select>
    <button onClick={() => {
      const key = name.trim();
      if (!key || Object.prototype.hasOwnProperty.call(variables, key)) { setError("变量名不能为空或重复"); return; }
      onChange({...variables, [key]: {type, description: "", default: type === "path" ? "" : null}}, values);
      setName(""); setError("");
    }}>添加变量</button>
    {error && <p role="alert">{error}</p>}
  </details>;
}
