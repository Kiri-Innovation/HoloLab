import { useCallback, useEffect, useId, useState } from "react";
import type { ComputeNode } from "../wire";
import { invalidateWorkflowPreparations } from "../api";
import { WorkflowAddButton, WorkflowPopover } from "./WorkflowPopover";

export interface DeviceBindings {
  workflow_id: string;
  revision: number;
  bindings: Record<string, string>;
}

export function useWorkflowDevices(workflowId: string | null, reconnect: number) {
  const [state, setState] = useState<DeviceBindings | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    setState(null); setError("");
    if (!workflowId) return;
    const abort = new AbortController();
    const refresh = async () => {
      try {
        const response = await fetch(`/api/workflows/${encodeURIComponent(workflowId)}/device-bindings`, {signal: abort.signal});
        if (!response.ok) throw new Error(await response.text());
        const next: DeviceBindings = await response.json();
        if (abort.signal.aborted) return;
        setState(previous => {
          if (previous?.workflow_id === next.workflow_id && previous.revision > next.revision) return previous;
          if (previous?.revision !== next.revision) invalidateWorkflowPreparations(workflowId);
          return next;
        });
        setError("");
      } catch (reason) { if (!abort.signal.aborted) setError(String(reason)); }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 10000);
    window.addEventListener("focus", refresh);
    return () => { abort.abort(); window.clearInterval(timer); window.removeEventListener("focus", refresh); };
  }, [workflowId, reconnect]);
  const current = state?.workflow_id === workflowId ? state : null;
  const bind = async (logical: string, nodeId: string | null) => {
    if (!current || !workflowId || saving) return;
    setSaving(true); setError("");
    try {
      const response = await fetch(`/api/workflows/${encodeURIComponent(workflowId)}/device-bindings`, {
        method: "PATCH", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({base_revision: current.revision, bindings: {[logical]: nodeId}}),
      });
      const data = await response.json();
      if (!response.ok) {
        if (data.detail?.current) { invalidateWorkflowPreparations(workflowId); setState(data.detail.current); }
        throw new Error(response.status === 409 ? "设备绑定已被修改，请重新选择。" : JSON.stringify(data.detail));
      }
      invalidateWorkflowPreparations(workflowId); setState(data);
    } catch (reason) { setError(String(reason)); }
    finally { setSaving(false); }
  };
  return {state: current, error, saving, bind};
}

export function logicalDevices(names: string[], bindings: Record<string, string> = {}, added: string[] = []) {
  return [...new Set(["default", ...names, ...Object.keys(bindings), ...added])].sort((a, b) => a === "default" ? -1 : b === "default" ? 1 : a.localeCompare(b));
}

export function deviceRoute(
  logical: string, bindings: Record<string, string> | undefined, legacy: string | null,
  nodes: Record<string, ComputeNode>, frozen = false,
) {
  const mapped = !frozen && bindings && Object.hasOwn(bindings, logical) ? bindings[logical] : undefined;
  const nodeId = mapped ?? legacy;
  if (frozen) {
    return { nodeId, label: nodeId ? `快照 · ${nodes[nodeId]?.node_name ?? nodeId}` : "快照无设备记录" };
  }
  if (!nodeId) return { nodeId, label: "自动分配（运行时确定）" };
  const node = nodes[nodeId];
  return { nodeId, label: `${mapped ? "显式绑定" : "旧版指派"} · ${node?.node_name ?? nodeId}${node ? "" : "（离线）"}` };
}

export function WorkflowDevices({names, nodes, devices, onAdd}: {
  names: string[]; nodes: ComputeNode[]; devices: ReturnType<typeof useWorkflowDevices>; onAdd: (name: string) => void;
}) {
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const dialogId = useId();
  const close = useCallback(() => setAnchor(null), []);
  return <section className="hl-workflow-devices" aria-label="设备">
    <header className="hl-workflow-devices-heading">
      <strong>逻辑设备映射</strong>
      <WorkflowAddButton label="添加设备" expanded={!!anchor} controls={dialogId}
        onClick={event => setAnchor(anchor ? null : event.currentTarget)} />
    </header>
    {names.map(logical => <label key={logical} className="hl-device-row">
      <span>{logical}</span>
      <select aria-label={`设备 ${logical}`} value={devices.state && Object.hasOwn(devices.state.bindings, logical) ? devices.state.bindings[logical] : ""}
        disabled={!devices.state || devices.saving} onChange={event => void devices.bind(logical, event.target.value || null)}>
        <option value="">自动分配（未绑定）</option>
        {devices.state && Object.hasOwn(devices.state.bindings, logical) && !nodes.some(node => node.node_id === devices.state!.bindings[logical]) &&
          <option value={devices.state.bindings[logical]}>{devices.state.bindings[logical]} · 离线</option>}
        {nodes.map(node => <option key={node.node_id} value={node.node_id}>{node.node_name} · {node.node_id.slice(0, 6)}</option>)}
      </select>
    </label>)}
    {anchor && <WorkflowPopover id={dialogId} name="添加设备" anchor={anchor} closeLabel="关闭添加设备" onClose={close}>
      {dismiss => <AddWorkflowDevice onAdd={name => { onAdd(name); dismiss(); }} onCancel={dismiss} />}
    </WorkflowPopover>}
    {devices.error && <p role="alert">{devices.error}</p>}
  </section>;
}

function AddWorkflowDevice({ onAdd, onCancel }: { onAdd: (name: string) => void; onCancel: () => void }) {
  const [name, setName] = useState("");
  return <form className="hl-workflow-add-form" onSubmit={event => {
    event.preventDefault();
    if (name.trim()) onAdd(name.trim());
  }}>
    <label className="hl-variable-row"><span>名称</span>
      <input aria-label="新逻辑设备名称" value={name} onChange={event => setName(event.target.value)}
        placeholder="逻辑设备名称" autoFocus />
    </label>
    <div className="hl-evidence-actions">
      <button className="hl-evidence-primary" type="submit" disabled={!name.trim()}>添加设备</button>
      <button type="button" onClick={onCancel}>取消</button>
    </div>
  </form>;
}
