import { useCallback, useId, useState } from "react";
import type { ComputeNode, VariableDeclaration, VariableDeclarations } from "../wire";
import { PathInput } from "./PathInput";
import { WorkflowAddButton, WorkflowPopover } from "./WorkflowPopover";

type VariableValues = Record<string, unknown>;
type ChangeVariables = (variables: VariableDeclarations, values: VariableValues, rename?: { from: string; to: string }) => void;

function WorkflowVariableValue({ name, type, value, browseNode, placeholder, onChange }: {
  name: string; type: VariableDeclaration["type"]; value: unknown; browseNode?: ComputeNode;
  placeholder?: string; onChange: (value: string | number | null) => void;
}) {
  const text = String(value ?? "");
  if (type === "path") return <PathInput name={name} value={text} optional placeholder={placeholder}
    nodeId={browseNode?.node_id ?? null} initialDirectory={browseNode?.workspace_root ?? "/"} onChange={onChange} />;
  return <input aria-label={name} type="number" step={type === "float" ? "any" : "1"}
    value={text} placeholder={placeholder} onChange={event => onChange(event.target.value === "" ? null : event.target.valueAsNumber)} />;
}

export function WorkflowVariables({ variables, values, browseNodeFor, onChange }: {
  variables: VariableDeclarations; values: VariableValues;
  browseNodeFor: (name: string) => ComputeNode | undefined;
  onChange: ChangeVariables;
}) {
  const [type, setType] = useState<VariableDeclaration["type"]>("path");
  const [editor, setEditor] = useState<{ name: string | null; anchor: HTMLButtonElement } | null>(null);
  const dialogId = useId();
  const closeEditor = useCallback(() => setEditor(null), []);
  const adding = editor?.name === null;
  const declaration = editor?.name ? variables[editor.name] : undefined;
  return <div className="hl-workflow-variables-panel">
    <details className="hl-evidence-panel hl-workflow-variables" onToggle={event => {
      if (!event.currentTarget.open && !adding) closeEditor();
    }}><summary>工作流变量（{Object.keys(variables).length}）</summary>
      <p>声明与默认值随配方共享；本地值只用于本工作流。在参数行选择同类型变量即可绑定。</p>
      {!Object.keys(variables).length && <p>尚未声明变量。添加路径、整数或浮点数变量，供多个参数共用。</p>}
      <ul className="hl-variable-list">
        {Object.entries(variables).map(([key, variable]) => {
          const expanded = editor?.name === key;
          const local = Object.hasOwn(values, key);
          const effective = local ? values[key] : variable.default;
          return <li className="hl-variable-list-row" key={key}>
            <strong title={key}>{key}</strong>
            <span className="hl-evidence-muted">{variable.type}</span>
            <div className="hl-variable-inline-value" data-value-source={local ? "local" : "default"}
              title={local ? "本地覆盖值" : "继承共享默认值"}>
              <WorkflowVariableValue name={`${key} 当前值`} type={variable.type} value={effective}
                placeholder={local ? "本地值为空" : "共享默认值为空"}
                browseNode={variable.type === "path" ? browseNodeFor(key) : undefined}
                onChange={value => onChange(variables, {...values, [key]: value})} />
            </div>
            <button type="button" aria-label={`编辑变量 ${key}`} aria-haspopup="dialog" aria-expanded={expanded}
              aria-controls={expanded ? dialogId : undefined}
              onClick={event => setEditor(expanded ? null : { name: key, anchor: event.currentTarget })}>⋯</button>
          </li>;
        })}
      </ul>
    </details>
    <WorkflowAddButton label="添加变量" expanded={adding} controls={dialogId}
      onClick={event => setEditor(adding ? null : { name: null, anchor: event.currentTarget })} />
    {editor && (adding || declaration) && <WorkflowPopover key={editor.name ?? ""} id={dialogId}
      name={editor.name ?? "添加变量"} anchor={editor.anchor}
      closeLabel={adding ? "关闭添加变量" : "关闭变量编辑"} onClose={closeEditor}>
      {close => editor.name !== null && declaration
        ? <WorkflowVariableFields variable={editor.name} declaration={declaration} variables={variables} values={values}
          browseNode={declaration.type === "path" ? browseNodeFor(editor.name) : undefined} onChange={onChange} onFinish={close} />
        : <AddWorkflowVariable variables={variables} initialType={type} onCancel={close} onAdd={(name, nextType) => {
          onChange({...variables, [name]: {type: nextType, description: "", default: nextType === "path" ? "" : null}}, values);
          setType(nextType);
          close();
        }} />}
    </WorkflowPopover>}
  </div>;
}

function AddWorkflowVariable({ variables, initialType, onAdd, onCancel }: {
  variables: VariableDeclarations; initialType: VariableDeclaration["type"];
  onAdd: (name: string, type: VariableDeclaration["type"]) => void; onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [type, setType] = useState(initialType);
  const [error, setError] = useState("");
  return <form className="hl-workflow-add-form" onSubmit={event => {
    event.preventDefault();
    const key = name.trim();
    if (!key || Object.hasOwn(variables, key)) { setError("变量名不能为空或重复"); return; }
    onAdd(key, type);
  }}>
    <label className="hl-variable-row"><span>名称</span>
      <input aria-label="新变量名" placeholder="变量名" value={name} onChange={event => setName(event.target.value)} autoFocus />
    </label>
    <label className="hl-variable-row"><span>类型</span>
      <select aria-label="新变量类型" value={type} onChange={event => setType(event.target.value as VariableDeclaration["type"])}>
        <option value="path">路径</option><option value="int">整数</option><option value="float">浮点数</option>
      </select>
    </label>
    {error && <p role="alert">{error}</p>}
    <div className="hl-evidence-actions">
      <button className="hl-evidence-primary" type="submit">添加变量</button>
      <button type="button" onClick={onCancel}>取消</button>
    </div>
  </form>;
}

export function WorkflowVariableFields({ variable: key, declaration, variables, values, browseNode, onChange, onFinish }: {
  variable: string; declaration: VariableDeclaration; variables: VariableDeclarations; values: VariableValues;
  browseNode?: ComputeNode; onChange: ChangeVariables; onFinish: () => void;
}) {
  const [renaming, setRenaming] = useState(false);
  const [newName, setNewName] = useState("");
  const [error, setError] = useState("");
  return <>
    <button type="button" onClick={() => { setRenaming(true); setNewName(key); }}>改名</button>
    {renaming && <form className="hl-evidence-actions" onSubmit={e => {
      e.preventDefault(); const to = newName.trim(); if (!to || (to !== key && Object.hasOwn(variables, to))) {
        setError("变量名不能为空或重复");
        return;
      }
      const next = Object.fromEntries(Object.entries(variables).map(([k, v]) => [k === key ? to : k, v]));
      const local = {...values};
      if (to !== key) {
        delete local[key];
        if (Object.prototype.hasOwnProperty.call(values, key)) local[to] = values[key];
        else delete local[to];
      }
      onChange(next, local, {from: key, to}); setRenaming(false); setError(""); onFinish();
    }}><input aria-label={`${key} 新名称`} value={newName} onChange={e => setNewName(e.target.value)} autoFocus />
      <button className="hl-evidence-primary" type="submit">保存名称</button><button type="button" onClick={() => { setRenaming(false); setError(""); }}>取消</button></form>}
    <label className="hl-variable-row"><span>类型</span><select aria-label={`${key} 类型`} value={declaration.type} onChange={e => {
      const nextType = e.target.value as VariableDeclaration["type"];
      if (!window.confirm(`修改 ${key} 的类型？已有值将保留，原有绑定可能因类型不匹配而无法执行。`)) return;
      onChange({...variables, [key]: {...declaration, type: nextType}}, values);
    }}><option value="path">路径 · path</option><option value="int">整数 · int</option><option value="float">浮点数 · float</option></select></label>
    <label className="hl-variable-row"><span>说明</span><input aria-label={`${key} 说明`} value={declaration.description ?? ""}
      onChange={e => onChange({...variables, [key]: {...declaration, description: e.target.value}}, values)} /></label>
    <div className="hl-variable-row"><span>共享默认值</span>
      <WorkflowVariableValue name={`${key} 默认值`} type={declaration.type} value={declaration.default} browseNode={browseNode}
        onChange={value => onChange({...variables, [key]: {...declaration, default: value}}, values)} />
    </div>
    {declaration.type !== "path" && [declaration.default, values[key]].some(value => value != null && typeof value !== "number") &&
      <p role="status">已保留原值（默认值：{String(declaration.default ?? "未提供")}；本地值：{String(values[key] ?? "未提供")}）。请填写{declaration.type === "int" ? "整数" : "浮点数"}，或改回原类型。</p>}
    <p>{Object.prototype.hasOwnProperty.call(values, key) ? '已提供本地值；清空输入仍保留本地值。' : '未提供本地值，执行时使用共享默认值。'}</p>
    <div className="hl-evidence-actions">
      <button disabled={!Object.prototype.hasOwnProperty.call(values, key)} onClick={() => { const next = {...values}; delete next[key]; onChange(variables, next); }}>使用默认值</button>
      <button className="hl-evidence-delete" onClick={() => {
        if (!window.confirm(`删除变量 ${key} 的声明和本地值？引用它的参数会保留绑定，需要重新选择变量或改用内联值。`)) return;
        const next = {...variables}, local = {...values}; delete next[key]; delete local[key]; onChange(next, local); onFinish();
      }}>删除声明</button>
    </div>
    {error && <p role="alert">{error}</p>}
  </>;
}
