// Portrait-phone layout for AppInner.
//
// Above the 900px breakpoint this file is inert — App.tsx only mounts
// MobileShell when the media query flips. Same panel components as the
// desktop grid, just rearranged: top half canvas, bottom half a five-tab
// switcher (PACKS / NODES / RUNS / JOBS / INSPECT) that reveals the
// same subtree the desktop docks around the edges. Fixed 60/40 split
// (canvas / bottom); no drag splitter for MVP.
//
// Auto tab-switch on state changes so the operator doesn't have to poke
// the bar between "select a node" and "watch its run":
//   - non-null selection edge triggers INSPECT
//   - a fresh latestSnapshotId (i.e. a run just happened) triggers JOBS
// Both use "skip the first observed value" refs so initial mount doesn't
// hijack the default PACKS tab.

import { useEffect, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";

export type MobileTab = "packs" | "nodes" | "runs" | "jobs" | "inspect";

export interface MobileShellProps {
  topbar: ReactNode;
  canvas: ReactNode;
  packs: ReactNode;
  nodes: ReactNode;
  runs: ReactNode | null;
  jobs: ReactNode;
  inspect: ReactNode;
  // Non-null when a node or edge is selected. Any change from null →
  // non-null (or non-null → different id) auto-switches to INSPECT.
  selectionId: string | null;
  // Snapshot id of the latest run. Any change (except the very first
  // observation on mount) auto-switches to JOBS.
  latestSnapshotId: string | null;
}

// Media-query hook, isolated so tests can override the ``matches`` shape
// if we ever add jsdom coverage for this file. SSR-safe (returns false).
export function useIsMobilePortrait(): boolean {
  const [match, setMatch] = useState<boolean>(() => {
    if (typeof window === "undefined" || !window.matchMedia) return false;
    return window.matchMedia("(max-width: 900px)").matches;
  });
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia("(max-width: 900px)");
    const onChange = () => setMatch(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  return match;
}

// Tab metadata: label + inline SVG glyph. Icons are 20px currentColor
// strokes so they inherit accent / muted from the parent button.
type TabDef = { key: MobileTab; label: string; icon: ReactNode };

const ICON_PROPS = {
  width: 20,
  height: 20,
  viewBox: "0 0 20 20",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.6,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
};

const TABS: readonly TabDef[] = [
  {
    key: "packs",
    label: "Packs",
    icon: (
      <svg {...ICON_PROPS}>
        <path d="M3 6l7-3 7 3v8l-7 3-7-3V6z" />
        <path d="M3 6l7 3 7-3" />
        <path d="M10 9v9" />
      </svg>
    ),
  },
  {
    key: "nodes",
    label: "Nodes",
    icon: (
      <svg {...ICON_PROPS}>
        <rect x="3" y="3.5" width="14" height="5" rx="1" />
        <rect x="3" y="11.5" width="14" height="5" rx="1" />
        <circle cx="6" cy="6" r="0.6" fill="currentColor" stroke="none" />
        <circle cx="6" cy="14" r="0.6" fill="currentColor" stroke="none" />
      </svg>
    ),
  },
  {
    key: "runs",
    label: "Runs",
    icon: (
      <svg {...ICON_PROPS}>
        <path d="M4 5h12" />
        <path d="M4 10h12" />
        <path d="M4 15h8" />
      </svg>
    ),
  },
  {
    key: "jobs",
    label: "Jobs",
    icon: (
      <svg {...ICON_PROPS}>
        <circle cx="10" cy="10" r="7" />
        <path d="M10 6v4l2.5 2" />
      </svg>
    ),
  },
  {
    key: "inspect",
    label: "Inspect",
    icon: (
      <svg {...ICON_PROPS}>
        <circle cx="10" cy="10" r="7" />
        <path d="M10 13v-4" />
        <circle cx="10" cy="7" r="0.6" fill="currentColor" stroke="none" />
      </svg>
    ),
  },
];

export function MobileShell({
  topbar,
  canvas,
  packs,
  nodes,
  runs,
  jobs,
  inspect,
  selectionId,
  latestSnapshotId,
}: MobileShellProps) {
  const [active, setActive] = useState<MobileTab>("packs");

  // Skip the value observed on mount so a stored selection / prior
  // snapshot id doesn't yank the operator off the default PACKS tab
  // the instant they arrive.
  const selSeenRef = useRef<string | null | undefined>(undefined);
  useEffect(() => {
    if (selSeenRef.current === undefined) {
      selSeenRef.current = selectionId;
      return;
    }
    if (selectionId && selectionId !== selSeenRef.current) {
      setActive("inspect");
    }
    selSeenRef.current = selectionId;
  }, [selectionId]);

  // Snapshot-driven auto-switch is asymmetric: the *first* non-null value we
  // observe is initial hydration (the workflow just finished loading its last
  // run), NOT a run the user just kicked off — hijacking to JOBS at that
  // moment would fight the default PACKS tab on every fresh page load.
  // Only after we've locked in one non-null baseline do subsequent changes
  // count as "a run just happened".
  const snapSeenRef = useRef<string | null>(null);
  const snapHydratedRef = useRef<boolean>(false);
  useEffect(() => {
    if (!latestSnapshotId) return;
    if (!snapHydratedRef.current) {
      snapHydratedRef.current = true;
      snapSeenRef.current = latestSnapshotId;
      return;
    }
    if (latestSnapshotId !== snapSeenRef.current) {
      setActive("jobs");
      snapSeenRef.current = latestSnapshotId;
    }
  }, [latestSnapshotId]);

  const panelForTab: Record<MobileTab, ReactNode> = {
    packs,
    nodes,
    runs: runs ?? (
      <div
        style={{
          padding: "var(--space-4)",
          fontSize: "var(--fs-sm)",
          color: "var(--text-muted)",
        }}
      >
        No workflow loaded.
      </div>
    ),
    jobs,
    inspect,
  };

  return (
    <div
      className="hl-app-shell hl-workspace hl-mobile"
      data-hl-mobile-active-tab={active}
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100vh",
        background: "var(--bg)",
        color: "var(--text-body)",
      }}
    >
      <div style={{ flex: "none" }}>{topbar}</div>

      <div
        style={{
          flex: 3,
          minHeight: 0,
          position: "relative",
          overflow: "hidden",
        }}
      >
        {canvas}
      </div>

      <div style={{ flex: "none", height: 1, background: "var(--border)" }} />

      <nav
        role="tablist"
        aria-label="Panels"
        style={{
          flex: "none",
          display: "grid",
          gridTemplateColumns: "repeat(5, 1fr)",
          background: "var(--surface)",
          borderTop: "1px solid var(--border)",
          borderBottom: "1px solid var(--border)",
        }}
      >
        {TABS.map((tab) => {
          const isActive = tab.key === active;
          const style: CSSProperties = {
            appearance: "none",
            border: "none",
            background: "transparent",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            gap: 2,
            minHeight: 52,
            padding: "6px 2px",
            cursor: "pointer",
            color: isActive ? "var(--accent)" : "var(--text-muted)",
            fontSize: "var(--fs-xs)",
            fontFamily: "inherit",
            borderTop: `2px solid ${isActive ? "var(--accent)" : "transparent"}`,
            marginTop: -1,
            transition: "color 120ms",
          };
          return (
            <button
              key={tab.key}
              type="button"
              role="tab"
              aria-selected={isActive}
              data-hl-mobile-tab={tab.key}
              onClick={() => setActive(tab.key)}
              style={style}
            >
              {tab.icon}
              <span>{tab.label}</span>
            </button>
          );
        })}
      </nav>

      <section
        role="tabpanel"
        aria-label={active}
        style={{
          flex: 2,
          minHeight: 0,
          overflow: "auto",
          background: "var(--surface)",
        }}
      >
        {panelForTab[active]}
      </section>
    </div>
  );
}
