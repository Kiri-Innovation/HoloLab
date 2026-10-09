import { Handle, Position, type NodeProps } from "@xyflow/react";
import { Preview } from "./previews";
import type { PreviewTarget } from "./AlgorithmNode";

export interface ViewNodeData extends Record<string, unknown> {
  kind: "view";
  title?: string | null;
  target?: PreviewTarget | null;
}

/** A non-executing canvas sticker for observing exactly one upstream handle. */
export function ViewNode({ data, selected }: NodeProps) {
  const d = data as ViewNodeData;
  const target = d.target ?? null;
  return (
    <div
      className="nodrag"
      style={{
        width: 300,
        minHeight: 124,
        border: `1px solid ${selected ? "var(--accent)" : "#d8b96a"}`,
        borderRadius: "var(--radius-md)",
        background: "#fff9df",
        color: "#443816",
        boxShadow: selected ? "0 0 0 2px var(--accent-soft)" : "var(--shadow-1)",
        overflow: "hidden",
      }}
    >
      <Handle type="target" position={Position.Left} id="in" style={{ background: "#c99722" }} />
      <div style={{ padding: "8px 10px", fontSize: 12, fontWeight: 700, borderBottom: "1px solid #ead99e" }}>
        ◈ {d.title?.trim() || "视图"}
      </div>
      <div style={{ padding: 8, fontSize: 11 }}>
        {!target ? (
          <span style={{ color: "#806f3b" }}>连接一个产物以查看；不会创建任务或产物。</span>
        ) : target.deleted ? (
          <span style={{ color: "var(--error)" }}>产物已被清理</span>
        ) : (
          <Preview
            spec={target.preview ?? undefined}
            baseUrl={target.proxy_url}
            storage={target.storage}
            handleId={target.handle_id}
            absolutePath={target.absolute_path}
            tags={target.tags}
            arrayed={Boolean(target.dim_labels?.length)}
            dimLabels={target.dim_labels ?? undefined}
          />
        )}
      </div>
    </div>
  );
}
