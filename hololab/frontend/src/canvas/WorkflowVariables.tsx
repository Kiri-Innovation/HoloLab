import { useState } from "react";
import type { VariableDeclarations } from "../wire";
import './workflowEvidence.css';

export function WorkflowVariables({ variables, values, onChange }: {
  variables: VariableDeclarations; values: Record<string, unknown>;
  onChange: (variables: VariableDeclarations, values: Record<string, unknown>, rename?: { from: string; to: string }) => void;
}) {
  const [name, setName] = useState("");
  const [type, setType] = useState<"path" | "int">("path");
  const [error, setError] = useState("");
  const [renaming, setRenaming] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const validName = (key: string, original?: string) => {
    if (!key || (key !== original && Object.prototype.hasOwnProperty.call(variables, key))) {
      setError("变量名不能为空或重复"); return false;
    }
    setError(""); return true;
  };
  return <details className="hl-evidence-panel hl-workflow-variables"><summary>工作流变量（{Object.keys(variables).length}）</summary>
    <p>声明与默认值随配方共享；本地值只用于本工作流。在参数行选择同类型变量即可绑定。</p>
    {!Object.keys(variables).length && <p>尚未声明变量。添加路径或整数变量，供多个参数共用。</p>}
    {Object.entries(variables).map(([key, declaration]) => <section className="hl-variable-card" key={key}>
      <header><strong>{key}</strong><span className="hl-evidence-muted">{declaration.type === 'int' ? '整数' : '路径'} · {declaration.type}</span>
        <button onClick={() => { setRenaming(key); setNewName(key); }}>改名</button></header>
      {renaming === key && <form className="hl-evidence-actions" onSubmit={e => {
        e.preventDefault(); const to = newName.trim(); if (!validName(to, key)) return;
        const next = Object.fromEntries(Object.entries(variables).map(([k, v]) => [k === key ? to : k, v]));
        const local = {...values};
        if (to !== key) {
          delete local[key];
          if (Object.prototype.hasOwnProperty.call(values, key)) local[to] = values[key];
          else delete local[to];
        }
        onChange(next, local, {from: key, to}); setRenaming(null);
      }}><input aria-label={`${key} 新名称`} value={newName} onChange={e => setNewName(e.target.value)} autoFocus />
        <button className="hl-evidence-primary" type="submit">保存名称</button><button type="button" onClick={() => setRenaming(null)}>取消</button></form>}
      <label className="hl-variable-row"><span>类型</span><select aria-label={`${key} 类型`} value={declaration.type} onChange={e => {
        const nextType = e.target.value as 'path' | 'int';
        if (!window.confirm(`修改 ${key} 的类型？已有值将保留，原有绑定可能因类型不匹配而无法执行。`)) return;
        onChange({...variables, [key]: {...declaration, type: nextType}}, values);
      }}><option value="path">路径 · path</option><option value="int">整数 · int</option></select></label>
      <label className="hl-variable-row"><span>说明</span><input aria-label={`${key} 说明`} value={declaration.description ?? ""}
        onChange={e => onChange({...variables, [key]: {...declaration, description: e.target.value}}, values)} /></label>
      <label className="hl-variable-row"><span>共享默认值</span><input aria-label={`${key} 默认值`} type={declaration.type === "int" ? "number" : "text"} step="1"
        value={String(declaration.default ?? "")} onChange={e => onChange({...variables,
          [key]: {...declaration, default: declaration.type === "int" ? (e.target.value === "" ? null : e.target.valueAsNumber) : e.target.value}}, values)} /></label>
      <label className="hl-variable-row"><span>本地值</span><input aria-label={`${key} 本地值`} type={declaration.type === "int" ? "number" : "text"} step="1"
        placeholder="未提供；使用默认值" value={String(values[key] ?? "")}
        onChange={e => onChange(variables, {...values, [key]: declaration.type === "int"
          ? (e.target.value === "" ? null : e.target.valueAsNumber) : e.target.value})} /></label>
      {declaration.type === "int" && [declaration.default, values[key]].some(value => value != null && typeof value !== "number") &&
        <p role="status">已保留原值（默认值：{String(declaration.default ?? "未提供")}；本地值：{String(values[key] ?? "未提供")}）。请填写整数，或改回原类型。</p>}
      <p>{Object.prototype.hasOwnProperty.call(values, key) ? '已提供本地值；清空输入仍保留本地值。' : '未提供本地值，执行时使用共享默认值。'}</p>
      <div className="hl-evidence-actions">
        <button disabled={!Object.prototype.hasOwnProperty.call(values, key)} onClick={() => { const next = {...values}; delete next[key]; onChange(variables, next); }}>使用默认值</button>
        <button className="hl-evidence-delete" onClick={() => {
          if (!window.confirm(`删除变量 ${key} 的声明和本地值？引用它的参数会保留绑定，需要重新选择变量或改用内联值。`)) return;
          const next = {...variables}, local = {...values}; delete next[key]; delete local[key]; onChange(next, local);
        }}>删除声明</button>
      </div>
    </section>)}
    <form className="hl-variable-add" onSubmit={e => {
      e.preventDefault(); const key = name.trim(); if (!validName(key)) return;
      onChange({...variables, [key]: {type, description: "", default: type === "path" ? "" : null}}, values);
      setName(""); setError("");
    }}>
      <input aria-label="新变量名" placeholder="变量名" value={name} onChange={e => setName(e.target.value)} />
      <select aria-label="新变量类型" value={type} onChange={e => setType(e.target.value as "path" | "int")}>
        <option value="path">路径</option><option value="int">整数</option>
      </select>
      <button className="hl-evidence-primary" type="submit">添加变量</button>
    </form>
    {error && <p role="alert">{error}</p>}
  </details>;
}
