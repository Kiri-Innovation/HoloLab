import type { CSSProperties } from "react";

/** Shared geometry for compact toolbar and panel-header affordances. */
export const CONTROL_STYLE: CSSProperties = {
  height: "var(--control-h-md)",
  padding: "0 12px",
  border: "1px solid var(--border-strong)",
  borderRadius: "var(--radius-sm)",
  background: "var(--surface)",
  color: "var(--text-body)",
  cursor: "pointer",
  fontSize: "var(--fs-sm)",
  fontWeight: 500,
  lineHeight: "var(--lh-ui)",
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  transition:
    "background var(--dur-fast) var(--ease), border-color var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease)",
};

export const PRIMARY_CONTROL_STYLE: CSSProperties = {
  ...CONTROL_STYLE,
  background: "var(--accent)",
  borderColor: "var(--accent)",
  color: "var(--accent-fg)",
  fontWeight: 600,
};

/** Non-interactive metadata and count summaries: quiet, borderless text chips. */
export const CHIP_STYLE: CSSProperties = {
  height: "var(--control-h-sm)",
  padding: 0,
  border: "none",
  borderRadius: 0,
  background: "transparent",
  color: "var(--text-muted)",
  fontSize: "var(--fs-xs)",
  lineHeight: "var(--lh-ui)",
  display: "inline-flex",
  alignItems: "center",
  gap: 5,
  fontVariantNumeric: "tabular-nums",
};
