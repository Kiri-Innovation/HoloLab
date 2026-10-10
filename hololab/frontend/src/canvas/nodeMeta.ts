// Narrow graph/canvas nodes at the algorithm/view boundary.
//
// View stickers intentionally have no pack identity. Keep that invariant in
// one place so interaction paths cannot dereference ``data.pack.name`` while
// a view is selected, dragged, or used as an edge target.

import type { Node } from "@xyflow/react";
import type { GraphNode } from "../wire";
import type { AlgorithmNodeData } from "./AlgorithmNode";
import type { ViewNodeData } from "./ViewNode";

export function isViewGraphNode(node: Pick<GraphNode, "kind">): boolean {
  return node.kind === "view";
}

export function isAlgorithmGraphNode(node: GraphNode): boolean {
  return !isViewGraphNode(node) && typeof node.algorithm_name === "string" && typeof node.algorithm_version === "string";
}

export function graphNodeLabel(node: GraphNode): string {
  return isViewGraphNode(node) ? node.title?.trim() || "视图" : node.algorithm_name;
}

export function viewNodeData(data: unknown): data is ViewNodeData {
  return typeof data === "object" && data !== null && (data as ViewNodeData).kind === "view";
}

export function algorithmNodeData(data: unknown): AlgorithmNodeData | null {
  if (viewNodeData(data)) return null;
  const candidate = data as Partial<AlgorithmNodeData> | null;
  return candidate?.pack ? (candidate as AlgorithmNodeData) : null;
}

export function algorithmDataForNode(node: Node<Record<string, unknown>>): AlgorithmNodeData | null {
  return algorithmNodeData(node.data);
}
