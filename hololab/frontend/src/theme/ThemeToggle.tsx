/**
 * Single-button theme cycler: light → system → dark → light.
 *
 * Clicking advances to the next mode; the icon shows the CURRENT mode
 * (so the user always knows what they're in, and the next click's result
 * is predictable). Uses CSS tokens directly; same compact control family
 * as other header buttons.
 */

import type { ReactNode } from "react";
import { useTheme } from "./theme";
import type { ThemePref } from "./theme";
import { CONTROL_STYLE } from "../ui/controlStyles";

const MODES: { value: ThemePref; label: string; nextLabel: string; icon: ReactNode }[] = [
  {
    value: "light",
    label: "Light theme",
    nextLabel: "Switch to system theme",
    icon: (
      <svg width="14" height="14" viewBox="0 0 20 20" fill="currentColor" aria-hidden>
        <path d="M10 3.5a.75.75 0 0 1 .75.75V6a.75.75 0 0 1-1.5 0V4.25A.75.75 0 0 1 10 3.5Zm0 10a.75.75 0 0 1 .75.75v1.75a.75.75 0 0 1-1.5 0V14.25a.75.75 0 0 1 .75-.75ZM3.5 10a.75.75 0 0 1 .75-.75H6a.75.75 0 0 1 0 1.5H4.25A.75.75 0 0 1 3.5 10Zm10 0a.75.75 0 0 1 .75-.75h1.75a.75.75 0 0 1 0 1.5H14.25a.75.75 0 0 1-.75-.75ZM5.05 5.05a.75.75 0 0 1 1.06 0l1.24 1.24a.75.75 0 1 1-1.06 1.06L5.05 6.11a.75.75 0 0 1 0-1.06Zm7.6 7.6a.75.75 0 0 1 1.06 0l1.24 1.24a.75.75 0 1 1-1.06 1.06l-1.24-1.24a.75.75 0 0 1 0-1.06Zm2.3-7.6a.75.75 0 0 1 0 1.06l-1.24 1.24a.75.75 0 1 1-1.06-1.06l1.24-1.24a.75.75 0 0 1 1.06 0Zm-7.6 7.6a.75.75 0 0 1 0 1.06l-1.24 1.24a.75.75 0 1 1-1.06-1.06l1.24-1.24a.75.75 0 0 1 1.06 0ZM10 7a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z" />
      </svg>
    ),
  },
  {
    value: "system",
    label: "System theme",
    nextLabel: "Switch to dark theme",
    icon: (
      <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <rect x="3" y="4" width="14" height="10" rx="1.4" />
        <path d="M7 17h6M10 14v3" />
      </svg>
    ),
  },
  {
    value: "dark",
    label: "Dark theme",
    nextLabel: "Switch to light theme",
    icon: (
      <svg width="14" height="14" viewBox="0 0 20 20" fill="currentColor" aria-hidden>
        <path d="M11.53 3.53a.75.75 0 0 0-.94-.94 7.5 7.5 0 1 0 8.82 8.82.75.75 0 0 0-.94-.94 5.5 5.5 0 0 1-6.94-6.94Z" />
      </svg>
    ),
  },
];

export function ThemeToggle() {
  const { pref, setPref } = useTheme();
  const idx = MODES.findIndex((m) => m.value === pref);
  const current = MODES[idx >= 0 ? idx : 0];
  const next = MODES[(idx + 1) % MODES.length];
  return (
    <button
      type="button"
      aria-label={current.nextLabel}
      title={current.nextLabel}
      onClick={() => setPref(next.value)}
      style={{
        ...CONTROL_STYLE,
        width: "var(--control-h-md)",
        padding: 0,
        justifyContent: "center",
        display: "inline-flex",
        alignItems: "center",
        color: "var(--text-subtle)",
        transition:
          "background var(--dur-fast) var(--ease), border-color var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease)",
      }}
    >
      {current.icon}
    </button>
  );
}
