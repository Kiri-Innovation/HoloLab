import type { AlgorithmNodeData } from "./AlgorithmNode";
import { typeTooltip } from "./typeTooltip";
import { Handle, useStore } from "@xyflow/react";
import type { ComponentProps } from "react";
import { type EdgeType } from "./edgeLabels";
import type { TypedEdgeData } from "./TypedEdge";
import { useTypeSummary } from "./useTypeSummary";

/** No wrapper or geometry changes: the original Handle remains the connection anchor. */
export function PortHandle({ nodeId, declared, handleId, asText, ...props }: ComponentProps<typeof Handle> & {
  asText?: boolean; nodeId: string; declared: EdgeType; handleId?: string;
}) {
  const node = useStore(s => s.nodeLookup.get(nodeId)?.data) as AlgorithmNodeData | undefined;
  const fanout = Boolean(node?.arrayed_toggle && node?.pack.arrayable);
  const raw = props.type === "target" ? node?.pack.inputs[props.id ?? ""] : node?.pack.outputs[props.id ?? ""];
  const edge = useStore(s => s.edges.find(e => props.type === "target"
    ? e.target === nodeId && e.targetHandle === props.id
    : e.source === nodeId && e.sourceHandle === props.id));
  const data = edge?.data as TypedEdgeData | undefined;
  const actual = useTypeSummary(data?.edgeType ?? declared, data?.handleId ?? handleId);
  const attributes = typeTooltip(props.id ?? '', props.type === 'source' ? data?.edgeType ?? declared : declared, actual, {fanout, handleId: data?.handleId ?? handleId, direction: props.type === 'target' ? 'input' : 'output', broadcast: props.type === 'target' && Boolean(raw?.scalar)}, raw);
  if (asText) return <span style={props.style} {...attributes}>{props.children}</span>;
  return <Handle {...props} className="hl-port-handle" {...attributes} />;
}
