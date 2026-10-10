import { Handle, Position, type NodeProps } from "@xyflow/react";
import { Preview } from "./previews";
import type { PreviewTarget } from "./AlgorithmNode";

export interface ViewNodeData extends Record<string, unknown> {
  kind: "view";
  title?: string | null;
  target?: PreviewTarget | null;
}

/** A non-executing canvas sticker for observing exactly one upstream handle. */
export function ViewNode({ id, data, selected }: NodeProps) {
  const d = data as ViewNodeData;
  const target = d.target ?? null;
  return (
    <div
      className="hololab-node"
      data-hl-view-node={id}
      style={{
        width: 300,
        minHeight: 124,
        background: "var(--surface-2)",
        border: `1px solid ${selected ? "var(--accent)" : "var(--border-strong)"}`,
        borderRadius: "var(--radius-md)",
        boxShadow: "var(--rf-node-shadow)",
        fontFamily: "var(--font-sans)",
        color: "var(--text-body)",
        position: "relative",
        transition: "width 120ms ease-out, box-shadow var(--dur-fast) var(--ease)",
        overflow: "hidden",
      }}
    >
      <Handle
        type="target"
        position={Position.Left}
        id="in"
        style={{
          background: "var(--text-muted)",
          borderRadius: "var(--radius-sm)",
        }}
      />
      <div
        className="hl-view-drag-handle"
        style={{
          padding: "var(--space-2) var(--space-3)",
          borderBottom: "1px solid var(--border)",
          background: "var(--surface-raised)",
          borderRadius: "var(--radius-md) var(--radius-md) 0 0",
          cursor: "grab",
          minHeight: 32,
          display: "flex",
          alignItems: "center",
        }}
      >
        <span
          style={{
            fontWeight: "var(--fw-semibold)",
            fontSize: "var(--fs-md)",
            lineHeight: "var(--lh-tight)",
            letterSpacing: "-0.005em",
            color: "var(--text)",
          }}
        >
          {d.title?.trim() || "视图"}
        </span>
      </div>
      <div className="nodrag" style={{ padding: "var(--space-2) var(--space-3)", fontSize: "var(--fs-xs)" }}>
        {!target ? (
          <span style={{ color: "var(--text-muted)" }}>连接一个产物以查看；不会创建任务或产物。</span>
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
