import type { Node } from "@xyflow/react";
import type { ViewNodeData } from "./ViewNode";
import { algorithmNodeData } from "./nodeMeta";

/** Duplicate editable draft configuration near its source, never results or wires. */
export function duplicateNode(source: Node<Record<string, unknown>>, id: string): Node<Record<string, unknown>> {
  const data = algorithmNodeData(source.data);
  const position = { x: source.position.x + 24, y: source.position.y + 24 };
  if (data) {
    const { runtime, previews, previewShards, nodeViewerFrames, previewOpen, onPreviewToggle, readOnly, pinnedParams, staleness, ...copyable } = data;
    return { id, type: "algorithm", position, selected: true, data: structuredClone(copyable) };
  }
  const view = source.data as ViewNodeData;
  return { id, type: "view", position, selected: true, dragHandle: ".hl-view-drag-handle", data: { kind: "view", title: view.title ?? null } };
}
