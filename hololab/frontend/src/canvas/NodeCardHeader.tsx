import { type ReactNode } from "react";
import { NodeStatusDot } from "../ui/NodeStatusTooltip";
import { NodeTitleTooltip } from "../ui/NodeTitleTooltip";
import { CopyRefButton } from "./CopyRefButton";
import { useCanvasContext } from "./CanvasContext";
import type { NodeStatusPresentation } from "./nodeStatus";
import { certificationMessage, type PackCertification } from "./packCertification";

/** Shared chrome for task, constant and observation cards. Actions retain their own semantics. */
export function NodeCardHeader({
  id,
  name,
  displayName = name,
  version = "",
  subtitle,
  device,
  state,
  status,
  certification = null,
  leadingActions,
  actions,
  children,
}: {
  id: string;
  name: string;
  /** Canvas-resolved label; retains scope only when this graph has a collision. */
  displayName?: string;
  version?: string;
  subtitle: string;
  device?: string;
  state?: string;
  status: NodeStatusPresentation;
  /** Why this pack is known to be reproducible, if applicable. */
  certification?: PackCertification;
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
        name={displayName}
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
        <span style={{ display: "flex", alignItems: "center", gap: 3, minWidth: 0 }}>
          <span
            style={{
              flex: "0 1 auto",
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
            {displayName}
          </span>
          {certification && <span
            data-hl-certified=""
            role="img"
            title={certificationMessage(certification)}
            aria-label={certificationMessage(certification)}
            style={{ color: "var(--status-success)", display: "inline-flex", flex: "0 0 auto" }}
          >
            <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M8 1.5 13 3.25v3.8c0 3.15-1.98 5.9-5 7.28-3.02-1.38-5-4.13-5-7.28v-3.8L8 1.5Z" />
              <path d="m5.35 7.85 1.7 1.55 3.6-4.15" />
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
            comment={`${displayName}${state ? ` · ${state}` : ""}`}
            size="xs"
            referenceDisplay={{
              title: version ? `${displayName}@${version}` : displayName,
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
