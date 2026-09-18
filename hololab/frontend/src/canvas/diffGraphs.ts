// Structural diff between two WorkflowGraph objects.
//
// Only structural fields are compared; cosmetic fields (position, preview_open)
// are intentionally excluded. Matching the "structural vs cosmetic" table in
// docs/workflow-schema.md.

import type { WorkflowGraph, GraphNode, GraphEdge } from "../wire";

export interface DiffItem {
  description: string;
}

// Stable structural fingerprint of one edge — used for set membership.
function edgeKey(e: GraphEdge): string {
  return `${e.source}\0${e.sourceHandle}\0${e.target}\0${e.targetHandle}`;
}

// Display name for a graph node: use algorithm_name as the human label.
function nodeName(n: GraphNode): string {
  return n.algorithm_name;
}

export function diffGraphs(draft: WorkflowGraph, snap: WorkflowGraph): DiffItem[] {
  const items: DiffItem[] = [];

  const snapById = new Map<string, GraphNode>(snap.nodes.map((n) => [n.id, n]));
  const draftById = new Map<string, GraphNode>(draft.nodes.map((n) => [n.id, n]));

  // Combined name lookup for edge descriptions (draft wins for added nodes).
  const nameById = new Map<string, string>();
  for (const n of snap.nodes) nameById.set(n.id, nodeName(n));
  for (const n of draft.nodes) nameById.set(n.id, nodeName(n));

  // --- nodes added ---
  for (const n of draft.nodes) {
    if (!snapById.has(n.id)) {
      items.push({ description: `新增节点 ${nodeName(n)}` });
    }
  }

  // --- nodes removed ---
  for (const n of snap.nodes) {
    if (!draftById.has(n.id)) {
      items.push({ description: `删除节点 ${nodeName(n)}` });
    }
  }

  // --- changed nodes (present in both) ---
  for (const dn of draft.nodes) {
    const sn = snapById.get(dn.id);
    if (!sn) continue;
    const label = nodeName(dn);

    if (
      dn.algorithm_name !== sn.algorithm_name ||
      dn.algorithm_version !== sn.algorithm_version
    ) {
      items.push({
        description: `节点 ${label} 算法: ${sn.algorithm_name}@${sn.algorithm_version} → ${dn.algorithm_name}@${dn.algorithm_version}`,
      });
    }

    if (dn.assigned_node_id !== sn.assigned_node_id) {
      items.push({
        description: `节点 ${label} 执行节点: ${sn.assigned_node_id ?? "未指定"} → ${dn.assigned_node_id ?? "未指定"}`,
      });
    }

    // arrayed_toggle — structural (fans out at dispatch). Old snapshots
    // predate this field; treat missing as false so a diff surfaces only
    // when the draft actually turns it on.
    const dArr = Boolean(dn.arrayed_toggle);
    const sArr = Boolean(sn.arrayed_toggle);
    if (dArr !== sArr) {
      items.push({
        description: `节点 ${label} arrayed 并行: ${sArr ? "开" : "关"} → ${dArr ? "开" : "关"}`,
      });
    }

    const dp = dn.params ?? {};
    const sp = sn.params ?? {};
    const keys = new Set([...Object.keys(dp), ...Object.keys(sp)]);
    for (const key of [...keys].sort()) {
      const dv = dp[key];
      const sv = sp[key];
      if (JSON.stringify(dv) !== JSON.stringify(sv)) {
        const from = key in sp ? JSON.stringify(sv) : "(未设置)";
        const to = key in dp ? JSON.stringify(dv) : "(未设置)";
        items.push({ description: `节点 ${label} 参数 ${key}: ${from} → ${to}` });
      }
    }
  }

  // --- edges added / removed ---
  const snapEdgeKeys = new Map<string, GraphEdge>(snap.edges.map((e) => [edgeKey(e), e]));
  const draftEdgeKeys = new Map<string, GraphEdge>(draft.edges.map((e) => [edgeKey(e), e]));

  const edgeDesc = (e: GraphEdge): string => {
    const src = nameById.get(e.source) ?? e.source;
    const tgt = nameById.get(e.target) ?? e.target;
    return `${src}.${e.sourceHandle} → ${tgt}.${e.targetHandle}`;
  };

  for (const [k, e] of draftEdgeKeys) {
    if (!snapEdgeKeys.has(k)) {
      items.push({ description: `新增连线: ${edgeDesc(e)}` });
    }
  }
  for (const [k, e] of snapEdgeKeys) {
    if (!draftEdgeKeys.has(k)) {
      items.push({ description: `断开连线: ${edgeDesc(e)}` });
    }
  }

  return items;
}
