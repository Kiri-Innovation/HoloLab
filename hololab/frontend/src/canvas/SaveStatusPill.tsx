// Autosave status pill — replaces the classic "Save" button.
//
// Five states drive the visual + click behaviour:
//
//   idle     — never dirty; empty pill, no action.
//   unsaved  — user edited; save is scheduled. Neutral surface.
//   saving   — request in flight. Info-tone chip.
//   saved    — last save succeeded. Success-tone chip (subtle).
//   error    — last save failed. Error-tone chip; click retries.
//   offline  — browser offline; will save on reconnect.
//
// Flat, no gradients, no glow — matches the app's stated visual
// language. State difference comes from background/text colour only,
// never a left-border strip.

import type { SaveStatus } from "./useDraftAutosave";

export interface SaveStatusPillProps {
  status: SaveStatus;
  onRetry: () => void;
}

interface Style {
  bg: string;
  fg: string;
  border: string;
  label: string;
  interactive: boolean;
}

function styleFor(status: SaveStatus): Style | null {
  switch (status) {
    case "idle":
      // Nothing to show — the pill collapses to zero width so the
      // toolbar doesn't reserve dead space for a workflow that has
      // never been edited.
      return null;
    case "unsaved":
      return {
        bg: "var(--surface-alt)",
        fg: "var(--text-muted)",
        border: "1px solid var(--border)",
        label: "Unsaved",
        interactive: false,
      };
    case "saving":
      return {
        bg: "var(--info-soft, var(--surface-alt))",
        fg: "var(--info, var(--text))",
        border: "1px solid var(--info, var(--border))",
        label: "Saving…",
        interactive: false,
      };
    case "saved":
      return {
        bg: "var(--surface-alt)",
        fg: "var(--text-subtle)",
        border: "1px solid var(--border)",
        label: "Saved",
        interactive: false,
      };
    case "error":
      return {
        bg: "var(--error-soft, var(--surface-alt))",
        fg: "var(--error)",
        border: "1px solid var(--error)",
        label: "Save failed · Retry",
        interactive: true,
      };
    case "offline":
      return {
        bg: "var(--surface-alt)",
        fg: "var(--text-muted)",
        border: "1px solid var(--border)",
        label: "Offline · Waiting",
        interactive: false,
      };
  }
}

export function SaveStatusPill({ status, onRetry }: SaveStatusPillProps) {
  const s = styleFor(status);
  if (s === null) return null;
  return (
    <button
      type="button"
      onClick={s.interactive ? onRetry : undefined}
      disabled={!s.interactive}
      title={
        status === "error"
          ? "click to retry saving the draft"
          : `autosave: ${s.label.toLowerCase()}`
      }
      data-hl-save-status={status}
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        height: "var(--control-h-sm)",
        padding: "0 10px",
        borderRadius: "var(--radius-sm)",
        border: s.border,
        background: s.bg,
        color: s.fg,
        fontSize: "var(--fs-xs)",
        fontWeight: 500,
        letterSpacing: "0.01em",
        cursor: s.interactive ? "pointer" : "default",
        // No transition on colour — the pill flips categories
        // instantly so the user knows the state changed rather than
        // seeing a mid-transition ambiguous colour.
      }}
    >
      {s.label}
    </button>
  );
}
