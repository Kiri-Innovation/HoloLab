import type { ReactNode } from "react";
import { NodeStatusDot } from "../ui/NodeStatusTooltip";
import { NodeTitleTooltip } from "../ui/NodeTitleTooltip";
import { CopyRefButton } from "./CopyRefButton";
import { useCanvasContext } from "./CanvasContext";
import type { NodeStatusPresentation } from "./nodeStatus";

/** Shared chrome for task, constant and observation cards. Actions retain their own semantics. */
export function NodeCardHeader({
  id,
  name,
  version = "",
  subtitle,
  device,
  state,
  status,
  leadingActions,
  actions,
  children,
}: {
  id: string;
  name: string;
  version?: string;
  subtitle: string;
  device?: string;
  state?: string;
  status: NodeStatusPresentation;
  leadingActions?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
}) {
  const { workflow_id: workflowId, workflow_name: workflowName } =
    useCanvasContext();
  return (
    <div
      style={{
        padding: "var(--space-2) var(--space-3)",
        borderBottom: "1px solid var(--border)",
        background: "var(--surface-raised)",
        borderRadius: "var(--radius-md) var(--radius-md) 0 0",
        display: "flex",
        alignItems: "center",
        gap: "var(--space-2)",
        minHeight: 32,
        position: "relative",
      }}
    >
      <NodeStatusDot state={state} status={status} />
      <NodeTitleTooltip
        name={name}
        version={version}
        device={device ?? subtitle}
        style={{
          flex: 1,
          minWidth: 0,
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          gap: 1,
        }}
      >
        <span
          style={{
            fontWeight: "var(--fw-semibold)",
            fontSize: "var(--fs-md)",
            color: "var(--text)",
            lineHeight: "var(--lh-tight)",
            letterSpacing: "-0.005em",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {name}
        </span>
        <span
          style={{
            fontSize: "var(--fs-micro)",
            fontFamily: "var(--font-mono)",
            color: "var(--text-muted)",
            lineHeight: 1,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {subtitle}
        </span>
      </NodeTitleTooltip>
      <div
        style={{
          flex: "0 0 auto",
          display: "flex",
          alignItems: "center",
          gap: "var(--space-1)",
        }}
      >
        {leadingActions}
        {workflowId && (
          <CopyRefButton
            kind="graph-node"
            id={`${workflowId}/${id}`}
            comment={`${name}${state ? ` · ${state}` : ""}`}
            size="xs"
            referenceDisplay={{
              title: version ? `${name}@${version}` : name,
              subtitle: `${id} · ${workflowName}`,
            }}
          />
        )}
        {actions}
      </div>
      {children}
    </div>
  );
}
