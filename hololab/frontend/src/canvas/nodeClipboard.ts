import type { Node } from "@xyflow/react";
import type { ViewNodeData } from "./ViewNode";
import { algorithmNodeData } from "./nodeMeta";

/** A graph-node copy contains editable draft configuration, never run results or wires. */
export function nodeClipboardCopy(node: Node<Record<string, unknown>>): Node<Record<string, unknown>> {
  const data = algorithmNodeData(node.data);
  if (data) {
    const { runtime, previews, previewShards, nodeViewerFrames, previewOpen, onPreviewToggle, readOnly, pinnedParams, staleness, ...copyable } = data;
    return { id: node.id, type: "algorithm", position: node.position, data: structuredClone(copyable) };
  }
  const view = node.data as ViewNodeData;
  return {
    id: node.id,
    type: "view",
    position: node.position,
    dragHandle: ".hl-view-drag-handle",
    data: { kind: "view", title: view.title ?? null },
  };
}

export function pastedNodePosition(screen: { x: number; y: number }): { x: number; y: number } {
  return { x: screen.x + 24, y: screen.y + 24 };
}
