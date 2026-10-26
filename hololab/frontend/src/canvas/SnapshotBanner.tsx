import { EvidenceValue } from "./EvidenceValue";
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
import { restoreFromSnapshot } from "../api";
import { stateColour } from "./AlgorithmNode";
import { IdentityEvidence } from "./CodeIdentityPanel";
import { PublishedAsNotes } from "./PublicationStatus";
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
        "用本次运行的图和参数覆盖当前草稿？\n\n历史运行记录会保留。",
      )
    ) {
      return;
    }
    setRestoring(true);
    try {
      await restoreFromSnapshot(snapshot.workflow_id, snapshot.snapshot_id);
      onRestored();
    } catch {
      setError("未能恢复草稿，请检查连接后重试。");
    } finally {
      setRestoring(false);
    }
  };

  return (
    <div
      className="hl-snapshot-banner"
      style={{
        position: "absolute",
        top: 0,
        left: 0,
        right: 0,
        display: "flex",
        alignItems: "center",
        gap: "var(--space-2)",
        padding: "var(--space-2) var(--space-3)",
        // Preserve the light-theme strip; dark mode uses themed surface tokens.
        background: "var(--snapshot-banner-bg)",
        color: "var(--text-inverse)",
        fontSize: "var(--fs-xs)",
        borderBottom: "1px solid var(--snapshot-banner-border)",
        boxShadow: "var(--shadow-1)",
        zIndex: 6,
      }}
    >
      <button
        type="button"
        onClick={onBack}
        data-tooltip="返回草稿" aria-label="返回草稿" aria-description="back to editable draft"
        style={{
          border: "1px solid var(--inverse-border)",
          background: "transparent",
          color: "var(--text-inverse)",
          padding: "0 var(--space-2)",
          borderRadius: "var(--radius-sm)",
          cursor: "pointer",
          fontSize: "var(--fs-xs)",
        }}
      >
        ‹ 返回草稿
      </button>
      <span
        style={{
          background: "var(--inverse-soft)",
          color: "var(--text-inverse)",
          padding: "var(--space-1) var(--space-2)",
          borderRadius: "var(--radius-sm)",
          fontSize: 10,
          fontWeight: 600,
          letterSpacing: "0.08em",
          textTransform: "uppercase",
        }}
      >
        只读快照
      </span>
      <span style={{ opacity: 0.85 }}>
        运行于{" "}
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
      <details>
        <summary>{Object.values(snapshot.code_evidence ?? {}).some(e => e.code_identity)
          ? "声明范围身份" : "历史运行未记录代码身份"}</summary>
        <div className="hl-evidence-panel hl-snapshot-evidence">
          {snapshot.variable_evidence && <details><summary>冻结的变量值</summary>
            <p>当次本地值与解析值；后续修改变量不会改写此记录。</p>
            <EvidenceValue value={snapshot.variable_evidence} />
          </details>}
          {Object.keys(snapshot.code_evidence ?? {}).length === 0
            ? <p>历史运行未记录代码身份；不会用当前代码回填。</p>
            : Object.entries(snapshot.code_evidence ?? {}).map(([nodeId, evidence]) => <details key={nodeId}>
              <summary>{nodeId} · {evidence.status === "recorded" ? "声明范围身份" : "历史运行未记录代码身份"}</summary>
              <IdentityEvidence identity={evidence.code_identity} effectiveParams={evidence.effective_params} />
              <PublishedAsNotes notes={snapshot.published_as?.[nodeId]} />
            </details>)}
        </div>
      </details>
      <CopyRefButton
        kind="run"
        id={snapshot.snapshot_id}
        comment={`run of workflow ${snapshot.workflow_id.slice(0, 8)} · ${snapshot.jobs.length} jobs · ${formatTs(snapshot.created_ts)}`}
        size="xs"
        onDark
      />
      <span style={{ display: "flex", gap: "var(--space-1)", alignItems: "center", marginLeft: "var(--space-1)" }}>
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
      {snapshot.preparation_error && <span role="alert">准备未完成，请返回草稿检查节点参数与本地能力。</span>}
      {error && <span style={{ color: "var(--error)" }}>{error}</span>}
      <button
        type="button"
        onClick={doRestore}
        disabled={restoring}
        className="hl-evidence-primary"
        data-tooltip={restoring ? "正在恢复" : "恢复快照"} aria-label={restoring ? "正在恢复" : "恢复快照"} aria-description="Overwrite the current draft with this run's graph and parameters"
        style={{
          padding: "0 var(--space-3)",
          borderRadius: "var(--radius-sm)",
          cursor: restoring ? "wait" : "pointer",
          fontSize: "var(--fs-xs)",
          fontWeight: 600,
          opacity: restoring ? 0.7 : 1,
          height: "var(--control-h-lg)",
        }}
      >
        {restoring ? "正在恢复…" : "恢复到草稿"}
      </button>
    </div>
  );
}

function formatTs(secs: number): string {
  const d = new Date(secs * 1000);
  const iso = d.toISOString();
  return `${iso.slice(0, 10)} ${iso.slice(11, 19)}`;
}
