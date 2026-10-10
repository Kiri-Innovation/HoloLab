import type { NodeStatusPresentation } from "../canvas/nodeStatus";

export function NodeStatusDot({ status, state }: { status: NodeStatusPresentation; state?: string }) {
  const text = `${status.conclusion}。${status.reason} ${status.action}`;
  return <span className="hl-node-status-slot">
    <span className="hl-node-status-hit nodrag" tabIndex={0} role="img"
      aria-label={text} data-hl-node-status={state || "idle"} data-status-tone={status.tone}
      data-tooltip={text} data-tooltip-status={status.conclusion}
      data-tooltip-reason={status.reason} data-tooltip-action={status.action}>
      <span className="hl-node-status-dot" aria-hidden="true" style={{ background: `var(--${status.tone})` }} />
    </span>
  </span>;
}

export function NodeStatusTooltipCard({ conclusion, reason, action }: Omit<NodeStatusPresentation, "tone">) {
  return <div className="hl-status-tooltip-content">
    <strong>{conclusion}</strong>
    <span>{reason}</span>
    <span className="hl-status-tooltip-action">{action}</span>
  </div>;
}
