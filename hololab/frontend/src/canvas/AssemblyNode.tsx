import { NodeCardHeader } from "./NodeCardHeader";
import { nodeStatus } from "./nodeStatus";
import { ConstantNode } from "./ConstantNode";
import { isConstantName } from "./constants";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Handle, Position, useReactFlow, useUpdateNodeInternals,
  useNodes, useEdges, type NodeProps,
} from "@xyflow/react";
import { AlgorithmNode, dispatchRunNode, type AlgorithmNodeData } from "./AlgorithmNode";
import { ASSEMBLE, assemblyFields, newAssembly, type AssemblyConfig } from "./assembly";
import { effectiveOutputType, formatTypeLabel } from "./edgeLabels";
import type { GraphNode } from "../wire";
import { NodeRunControl, canStopNode } from "./NodeRunControl";
import { newId } from "../newId";

/** Share the existing executable-node plumbing, not pack execution semantics. */
export function ExecutableNode(props: NodeProps) {
  if (isConstantName((props.data as AlgorithmNodeData).pack?.name ?? "")) return <ConstantNode {...props} />;
  return (props.data as AlgorithmNodeData).pack?.name === ASSEMBLE
    ? <AssemblyNode {...props} /> : <AlgorithmNode {...props} />;
}

export function AssemblyNode({ id, data }: NodeProps) {
  const d = data as AlgorithmNodeData & { assembly?: AssemblyConfig; readOnly?: boolean };
  const [runPending, setRunPending] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const runLock = useRef(false);
  async function run() {
    if (d.readOnly || runLock.current || canStopNode(d.runtime?.state)) return;
    runLock.current = true;
    setRunPending(true);
    setRunError(null);
    try { await dispatchRunNode(id); }
    catch (error) { setRunError(error instanceof Error ? error.message : String(error)); }
    finally { runLock.current = false; setRunPending(false); }
  }
  const a = d.assembly ?? newAssembly();
  const rf = useReactFlow();
  const nodes = useNodes();
  const edges = useEdges();
  const connected = edges.filter(e => e.target === id).map(e => e.targetHandle ?? "");
  const previousConnected = useRef<string[]>(connected);

  // Reclaim disconnected slots without discarding empty slots just added by
  // the user. Slot identities of remaining wires must never be renumbered.
  useEffect(() => {
    const removed = previousConnected.current.filter(s => !connected.includes(s));
    previousConnected.current = connected;
    if (d.readOnly || !removed.length) return;
    const slots = [...a.slots];
    for (const slot of removed) {
      if (slots.length > 2 && slots.includes(slot)) slots.splice(slots.indexOf(slot), 1);
    }
    const anchor = connected.includes(a.type_anchor_slot ?? "")
      ? a.type_anchor_slot : slots.find(s => connected.includes(s)) ?? null;
    rf.updateNodeData(id, { assembly: { ...a, slots, type_anchor_slot: anchor } });
  }, [connected.join("|"), a, id, rf, d.readOnly]);

  const typeLabel = useMemo(() => {
    const graphNodes = nodes.flatMap(n => {
      const data = n.data as AlgorithmNodeData;
      return data.pack ? [{
        id: n.id, ...assemblyFields(data.assembly),
        algorithm_name: data.pack.name, algorithm_version: data.pack.version,
        position: n.position, params: data.params ?? {},
        assigned_node_id: data.assigned_node_id, arrayed_toggle: data.arrayed_toggle,
      } as GraphNode] : [];
    });
    const catalogByKey = new Map(nodes.flatMap(n => {
      const data = n.data as AlgorithmNodeData;
      return data.pack ? [[`${data.pack.name}@${data.pack.version}`, data.pack] as const] : [];
    }));
    const type = effectiveOutputType(id, "out", {
      nodes: graphNodes,
      edges: edges.map(e => ({
        ...e, sourceHandle: e.sourceHandle ?? "", targetHandle: e.targetHandle ?? "",
      })),
      catalogByKey,
    });
    return type.tags.length ? formatTypeLabel(type) : "array<T> · 未绑定";
  }, [id, nodes, edges]);

  const remeasure = useUpdateNodeInternals();
  useEffect(() => { remeasure(id); }, [id, a.slots.join("|"), remeasure]);
  function update(next: AssemblyConfig) { rf.updateNodeData(id, { assembly: next }); }
  function remove(slot: string) {
    const slots = a.slots.filter(s => s !== slot);
    while (slots.length < 2) slots.push(`slot-${newId()}`);
    rf.setEdges(es => es.filter(e => e.target !== id || e.targetHandle !== slot));
    update({ ...a, slots, type_anchor_slot: a.type_anchor_slot === slot ? null : a.type_anchor_slot });
  }
  function move(index: number, step: number) {
    const slots = [...a.slots];
    const target = index + step;
    if (target < 0 || target >= slots.length) return;
    [slots[index], slots[target]] = [slots[target], slots[index]];
    update({ ...a, slots });
  }

  return (
    <div className="hololab-node" data-hl-intrinsic="array-assemble" style={{
      width: 320, background: "var(--surface-2)", color: "var(--text-body)",
      border: "1px solid var(--border)", borderRadius: 8,
    }}>
      <NodeCardHeader id={id} name="array-assemble" version={d.pack?.version ?? "1"} subtitle="system"
        state={d.runtime?.state} status={nodeStatus({ state: d.runtime?.state, stale: d.staleness, failReason: d.runtime?.fail_reason, readOnly: d.readOnly })}
        actions={!d.readOnly && <NodeRunControl id={id} pending={runPending} state={d.runtime?.state}
          jobId={d.runtime?.job_id} stale={!!d.staleness} onRun={run} />} />
      {runError && <div role="alert" className="nodrag nopan" style={{ padding: "6px 12px", color: "var(--status-failed)", overflowWrap: "anywhere" }}>运行失败：{runError}</div>}
      <div style={{ position: "relative", padding: "8px 16px", textAlign: "right" }}>
        {typeLabel}<Handle id="out" type="source" position={Position.Right} style={{ right: 6, background: "var(--text-muted)" }} />
      </div>
      {a.slots.map((slot, index) => (
        <div key={slot} style={{ position: "relative", padding: "8px 16px", display: "flex", gap: 8, alignItems: "center" }}>
          <Handle id={slot} type="target" position={Position.Left} style={{ left: 6, background: "var(--text-muted)" }} />
          <span style={{ flex: 1 }}>输入 {index + 1}</span>
          {!d.readOnly && <span className="nodrag nopan">
            <button aria-label={`上移输入 ${index + 1}`} disabled={index === 0} onClick={() => move(index, -1)}>↑</button>
            <button aria-label={`下移输入 ${index + 1}`} disabled={index === a.slots.length - 1} onClick={() => move(index, 1)}>↓</button>
            <button aria-label={`删除输入 ${index + 1}`} onClick={() => remove(slot)}>×</button>
          </span>}
        </div>
      ))}
      <div className="nodrag nopan" style={{ padding: 12, display: "flex", gap: 8 }}>
        {!d.readOnly && <button onClick={() => update({ ...a, slots: [...a.slots, `slot-${newId()}`] })}>添加输入</button>}
        <label>轴 <input aria-label="数组轴名" style={{ width: 90 }} value={a.axis_label} disabled={d.readOnly}
          onChange={e => update({ ...a, axis_label: e.target.value })} /></label>
      </div>
    </div>
  );
}
