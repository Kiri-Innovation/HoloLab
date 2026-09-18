// Top bar: workflow name + autosave status pill + Run button.
//
// The classic Save button has been retired — the draft autosaves 800 ms
// after any user edit (see ``useDraftAutosave``). ``saveStatus`` +
// ``onSaveRetry`` drive the pill that replaces it: quiescent when
// nothing's changed, ``Saving…`` mid-request, ``Save failed · Retry``
// on network error (click to reattempt), etc.

import { useState } from "react";
import type { RunSummary } from "./RecentJobsPanel";
import { STATE_COLOURS } from "./AlgorithmNode";
import { SaveStatusPill } from "./SaveStatusPill";
import { ThemeToggle } from "../theme/ThemeToggle";
import { CHIP_STYLE, CONTROL_STYLE, PRIMARY_CONTROL_STYLE } from "../ui/controlStyles";
import type { SaveStatus } from "./useDraftAutosave";

export interface WorkflowToolbarProps {
  workflowId: string | null;
  name: string;
  connected: boolean;
  running: boolean;
  summary: RunSummary | null;
  onNameChange: (name: string) => void;
  onRun: () => Promise<void>;
  // Navigate back to the Gallery landing. Replaces the old "new" and
  // "load" buttons — creating and switching between workflows is now
  // driven from the Gallery cards, not the canvas toolbar.
  onExitToGallery: () => void;
  // Autosave status + retry hook (see ``useDraftAutosave``).
  saveStatus: SaveStatus;
  onSaveRetry: () => Promise<void>;
  // True when the in-memory draft structurally differs from the latest
  // snapshot. Renders a badge near the Run button to signal that clicking
  // Run will Fork a new snapshot. Hidden when false.
  draftModified?: boolean;
}

export function WorkflowToolbar({
  workflowId,
  name,
  connected,
  running,
  summary,
  onNameChange,
  onRun,
  onExitToGallery,
  saveStatus,
  onSaveRetry,
  draftModified = false,
}: WorkflowToolbarProps) {
  const [message, setMessage] = useState<string>("");
  const [messageColour, setMessageColour] = useState<string>("var(--text-muted)");

  const flash = (m: string, colour = "var(--text-muted)") => {
    setMessage(m);
    setMessageColour(colour);
    window.setTimeout(() => setMessage(""), 4000);
  };

  const doRun = async () => {
    try {
      await onRun();
      flash("run started", "var(--success)");
    } catch (e) {
      flash(`run failed: ${(e as Error).message}`, "var(--error)");
    }
  };

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        height: 44,
        padding: "0 12px",
        borderBottom: "1px solid var(--border)",
        background: "var(--bg)",
        fontSize: "var(--fs-sm)",
      }}
    >
      <button
        type="button"
        onClick={onExitToGallery}
        title="back to workflows"
        style={{
          ...CONTROL_STYLE,
          fontWeight: 600,
          color: "var(--text)",
          gap: 6,
          letterSpacing: "-0.01em",
        }}
      >
        <span style={{ color: "var(--text-subtle)", fontSize: "var(--fs-md)" }}>‹</span>
        HoloLab
      </button>
      <div
        style={{
          width: 1,
          height: "var(--control-h-sm)",
          background: "var(--border)",
          margin: "0 4px",
        }}
      />
      <input
        value={name}
        onChange={(e) => onNameChange(e.target.value)}
        placeholder="workflow name"
        className="wf-name-input"
        style={{
          ...CONTROL_STYLE,
          flex: "0 1 240px",
          padding: "0 8px",
          color: "var(--text)",
          fontWeight: 600,
        }}
      />
      {workflowId && (
        <span
          style={{
            ...CHIP_STYLE,
            fontFamily: "var(--font-mono)",
            letterSpacing: "0.02em",
          }}
        >
          {workflowId.slice(0, 8)}
        </span>
      )}
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 5,
          height: "var(--control-h-sm)",
          fontSize: "var(--fs-xs)",
          color: connected ? "var(--success)" : "var(--error)",
        }}
      >
        <span
          style={{
            width: 6,
            height: 6,
            borderRadius: "var(--radius-pill)",
            background: connected ? "var(--success)" : "var(--error)",
          }}
        />
        {connected ? "connected" : "connecting…"}
      </span>
      {summary && summary.total > 0 && (
        <span
          style={{
            ...CHIP_STYLE,
            gap: 8,
          }}
          title="jobs in the current workflow: done / running / failed / pending"
        >
          <SummaryPip label={summary.done} colour={STATE_COLOURS.done} />
          <SummaryPip label={summary.running} colour={STATE_COLOURS.running} />
          <SummaryPip label={summary.failed} colour={STATE_COLOURS.failed} />
          <SummaryPip label={summary.pending} colour={STATE_COLOURS.pending} />
          <span style={{ color: "var(--text-subtle)" }}>of {summary.total}</span>
        </span>
      )}
      <div style={{ flex: 1 }} />
      <span
        style={{
          color: messageColour,
          fontSize: "var(--fs-xs)",
          minWidth: 100,
          textAlign: "right",
          height: "var(--control-h-sm)",
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "flex-end",
          transition: "opacity var(--dur-fast) var(--ease)",
        }}
      >
        {message}
      </span>
      <ThemeToggle />
      <SaveStatusPill
        status={saveStatus}
        onRetry={() => {
          void onSaveRetry();
        }}
      />
      {draftModified && (
        <span
          data-hl-draft-modified=""
          title="Draft has structural changes since the last snapshot. Running will fork a new snapshot."
          style={{
            fontSize: "var(--fs-xs)",
            color: "var(--warning, #c8a200)",
            whiteSpace: "nowrap",
          }}
        >
          已修改 · 运行将创建新快照
        </span>
      )}
      <button
        style={{ ...PRIMARY_CONTROL_STYLE, opacity: running ? 0.7 : 1 }}
        onClick={doRun}
        disabled={running}
      >
        {running ? "Running…" : "Run"}
      </button>
    </div>
  );
}

function SummaryPip({ label, colour }: { label: number; colour: string }) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        color: label > 0 ? "var(--text)" : "var(--text-subtle)",
        fontVariantNumeric: "tabular-nums",
      }}
    >
      <span
        style={{
          width: 7,
          height: 7,
          borderRadius: "var(--radius-pill)",
          background: colour,
          opacity: label > 0 ? 1 : 0.35,
        }}
      />
      {label}
    </span>
  );
}
