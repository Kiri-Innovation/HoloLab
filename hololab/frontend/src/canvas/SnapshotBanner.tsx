// Top-of-canvas strip that appears only while the user is viewing a
// snapshot (past run) instead of the editable draft. Clear visual
// affordance for "you are in read-only mode".
//
// Actions:
//   * back to draft — leaves the draft untouched
//   * clone → draft — overwrites the current draft with this snapshot's
//     graph after confirm(); triggers a draft reload in the App.

import { useState } from "react";
import type { SnapshotDetail } from "../wire";
import { ApiError, restoreFromSnapshot } from "../api";
import { stateColour } from "./AlgorithmNode";
import { CopyRefButton } from "./CopyRefButton";

export interface SnapshotBannerProps {
  snapshot: SnapshotDetail;
  onBack: () => void;
  // Fired after a successful restore so the App can reload the draft +
  // exit snapshot mode. Doing both in one place keeps the transition
  // atomic from the user's perspective.
  onRestored: () => void;
}

export function SnapshotBanner({ snapshot, onBack, onRestored }: SnapshotBannerProps) {
  const [restoring, setRestoring] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Compact per-state rollup so the banner tells you at a glance whether
  // the run succeeded, is still moving, or blew up — without needing to
  // click every node.
  const counts: Record<string, number> = {};
  for (const j of snapshot.jobs) counts[j.state] = (counts[j.state] ?? 0) + 1;

  const doRestore = async () => {
    if (
      !window.confirm(
        "Overwrite the current draft with this run's graph and parameters?\n\n" +
          "This does not delete the run itself.",
      )
    ) {
      return;
    }
    setRestoring(true);
    try {
      await restoreFromSnapshot(snapshot.workflow_id, snapshot.snapshot_id);
      onRestored();
    } catch (e) {
      const msg = e instanceof ApiError ? `HTTP ${e.status}` : (e as Error).message;
      setError(`restore failed: ${msg}`);
    } finally {
      setRestoring(false);
    }
  };

  return (
    <div
      style={{
        position: "absolute",
        top: 0,
        left: 0,
        right: 0,
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "8px 14px",
        // Dark strip so the mode-switch is unmistakable against the
        // draft canvas — inverted from body text/bg so it works under
        // both light and dark themes.
        background: "var(--text)",
        color: "var(--text-inverse)",
        fontSize: "var(--fs-xs)",
        borderBottom: "1px solid var(--text)",
        boxShadow: "var(--shadow-1)",
        zIndex: 6,
      }}
    >
      <button
        type="button"
        onClick={onBack}
        title="back to editable draft"
        style={{
          border: "1px solid var(--inverse-border)",
          background: "transparent",
          color: "var(--text-inverse)",
          padding: "3px 10px",
          borderRadius: "var(--radius-sm)",
          cursor: "pointer",
          fontSize: "var(--fs-xs)",
        }}
      >
        ‹ back to draft
      </button>
      <span
        style={{
          background: "var(--inverse-soft)",
          color: "var(--text-inverse)",
          padding: "2px 8px",
          borderRadius: "var(--radius-sm)",
          fontSize: 10,
          fontWeight: 600,
          letterSpacing: "0.08em",
          textTransform: "uppercase",
        }}
      >
        read-only
      </span>
      <span style={{ opacity: 0.85 }}>
        run from{" "}
        <strong style={{ fontVariantNumeric: "tabular-nums" }}>
          {formatTs(snapshot.created_ts)}
        </strong>
      </span>
      <span
        style={{
          fontFamily: "var(--font-mono)",
          opacity: 0.55,
          fontSize: 10,
        }}
      >
        {snapshot.snapshot_id.slice(0, 8)}
      </span>
      <CopyRefButton
        kind="run"
        id={snapshot.snapshot_id}
        comment={`run of workflow ${snapshot.workflow_id.slice(0, 8)} · ${snapshot.jobs.length} jobs · ${formatTs(snapshot.created_ts)}`}
        size="xs"
        onDark
      />
      <span style={{ display: "flex", gap: 6, alignItems: "center", marginLeft: 6 }}>
        {["done", "failed", "running", "pending", "assigned", "cancelled", "orphaned"]
          .filter((s) => (counts[s] ?? 0) > 0)
          .map((s) => (
            <span
              key={s}
              title={`${counts[s]} ${s}`}
              style={{ display: "inline-flex", alignItems: "center", gap: 3 }}
            >
              <span
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: 999,
                  background: stateColour(s),
                }}
              />
              {counts[s]}
            </span>
          ))}
      </span>
      <div style={{ flex: 1 }} />
      {error && <span style={{ color: "var(--error)" }}>{error}</span>}
      <button
        type="button"
        onClick={doRestore}
        disabled={restoring}
        title="Overwrite the current draft with this run's graph and parameters"
        style={{
          border: "1px solid var(--accent)",
          background: restoring ? "var(--inverse-soft)" : "var(--accent)",
          color: "var(--accent-fg)",
          padding: "3px 12px",
          borderRadius: "var(--radius-sm)",
          cursor: restoring ? "wait" : "pointer",
          fontSize: "var(--fs-xs)",
          fontWeight: 600,
          opacity: restoring ? 0.7 : 1,
          height: "var(--control-h-lg)",
        }}
      >
        {restoring ? "restoring…" : "clone → draft"}
      </button>
    </div>
  );
}

function formatTs(secs: number): string {
  const d = new Date(secs * 1000);
  const iso = d.toISOString();
  return `${iso.slice(0, 10)} ${iso.slice(11, 19)}`;
}
