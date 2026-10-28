import { type ReactNode } from "react";
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
  communityCertified = false,
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
  /** Community-scoped pack whose local content matches its registry release. */
  communityCertified?: boolean;
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
        <span style={{ display: "flex", alignItems: "center", gap: 4, minWidth: 0 }}>
          <span
            style={{
              flex: 1,
              minWidth: 0,
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
          {communityCertified && <span
            data-hl-community-certified=""
            role="img"
            title="社区认证版本：与注册表发布内容一致"
            aria-label="社区认证版本：与注册表发布内容一致"
            style={{ color: "var(--status-success)", display: "inline-flex", flex: "0 0 auto" }}
          >
            <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
              <path d="M8 1.1 13.5 3v4.05c0 3.44-2.2 6.42-5.5 7.85C4.7 13.47 2.5 10.49 2.5 7.05V3L8 1.1Zm0 2.02L4.5 4.34v2.71c0 2.37 1.42 4.53 3.5 5.74 2.08-1.21 3.5-3.37 3.5-5.74V4.34L8 3.12Zm2.26 2.55-2.9 3.36-1.63-1.4-1.04 1.2 2.84 2.44 3.94-4.57-1.21-1.04Z" />
            </svg>
          </span>}
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
