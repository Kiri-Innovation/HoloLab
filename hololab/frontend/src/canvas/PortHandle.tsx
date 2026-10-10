import { Handle, useStore } from "@xyflow/react";
import type { ComponentProps } from "react";
import { formatTypeLabelLong, type EdgeType } from "./edgeLabels";
import type { TypedEdgeData } from "./TypedEdge";
import { useTypeSummary } from "./useTypeSummary";

/** No wrapper or geometry changes: the original Handle remains the connection anchor. */
export function PortHandle({ nodeId, declared, handleId, ...props }: ComponentProps<typeof Handle> & {
  nodeId: string; declared: EdgeType; handleId?: string;
}) {
  const edge = useStore(s => s.edges.find(e => props.type === "target"
    ? e.target === nodeId && e.targetHandle === props.id
    : e.source === nodeId && e.sourceHandle === props.id));
  const data = edge?.data as TypedEdgeData | undefined;
  const actual = useTypeSummary(data?.edgeType ?? declared, data?.handleId ?? handleId);
  const type = formatTypeLabelLong(actual);
  const text = props.type === "target"
    ? edge ? `输入 ${props.id} · 已连接：${type}\n要求：${formatTypeLabelLong(declared)}` : `输入 ${props.id} · 要求：${type}`
    : `输出 ${props.id} · ${type}`;
  return <Handle {...props} className="hl-port-handle" data-tooltip={text} aria-label={text} />;
}
