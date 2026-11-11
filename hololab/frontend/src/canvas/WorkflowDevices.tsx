import { useEffect, useState } from "react";
import type { ComputeNode } from "../wire";
import { invalidateWorkflowPreparations } from "../api";

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

export function WorkflowDevices({names, nodes, devices, onAdd}: {
  names: string[]; nodes: ComputeNode[]; devices: ReturnType<typeof useWorkflowDevices>; onAdd: (name: string) => void;
}) {
  const [name, setName] = useState("");
  return <section className="hl-workflow-devices" aria-label="设备">
    <strong>设备</strong>
    {names.map(logical => <label key={logical} className="hl-device-row">
      <span>{logical}</span>
      <select aria-label={`设备 ${logical}`} value={devices.state?.bindings[logical] ?? ""}
        disabled={!devices.state || devices.saving} onChange={event => void devices.bind(logical, event.target.value || null)}>
        <option value="">自动分配（未绑定）</option>
        {devices.state?.bindings[logical] && !nodes.some(node => node.node_id === devices.state!.bindings[logical]) &&
          <option value={devices.state.bindings[logical]}>{devices.state.bindings[logical]} · 离线</option>}
        {nodes.map(node => <option key={node.node_id} value={node.node_id}>{node.node_name} · {node.node_id.slice(0, 6)}</option>)}
      </select>
    </label>)}
    <form onSubmit={event => { event.preventDefault(); if (name.trim()) { onAdd(name.trim()); setName(""); } }}>
      <input aria-label="新逻辑设备名称" value={name} onChange={event => setName(event.target.value)} placeholder="逻辑设备名称" />
      <button type="submit" disabled={!name.trim()}>添加设备</button>
    </form>
    {devices.error && <p role="alert">{devices.error}</p>}
  </section>;
}
