import type { CSSProperties } from "react";

/** A submitted job is live, but its algorithm process has not started yet. */
export function isJobAwaitingStart(state: string | undefined): boolean {
  return state === "loading" || state === "pending" || state === "assigned";
}

function stateColour(state: string | undefined, fallback: string): string {
  switch (state) {
    case "running": return "var(--status-running)";
    case "done": return "var(--status-done)";
    case "failed": return "var(--status-failed)";
    case "cancelled": return "var(--status-cancelled)";
    case "orphaned": return "var(--status-orphaned)";
    default: return fallback;
  }
}

/**
 * Shared status mark for canvas cards and Recent jobs.  It deliberately
 * separates "accepted/queued" from "algorithm process running": only the
 * latter is blue; accepted work is a neutral spinner.
 */
export function JobStateMarker({
  state,
  fallbackColour = "var(--status-pending)",
  className,
  style,
}: {
  state?: string;
  fallbackColour?: string;
  className?: string;
  style?: CSSProperties;
}) {
  const waiting = isJobAwaitingStart(state);
  return <span
    aria-hidden="true"
    data-hl-job-state={state ?? "idle"}
    data-hl-job-waiting={waiting || undefined}
    className={`hl-job-state-marker${waiting ? " hl-job-state-marker--waiting" : ""}${className ? ` ${className}` : ""}`}
    style={waiting ? style : { ...style, background: stateColour(state, fallbackColour) }}
  />;
}
