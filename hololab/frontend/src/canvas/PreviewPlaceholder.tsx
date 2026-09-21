// Empty-state placeholder for the AlgorithmNode preview drawer.
//
// Three states — all wear the same dark-shell chrome the real viewers
// use (see PREVIEW_SHELL in previews.tsx) so a switch from placeholder
// to real preview doesn't shift layout:
//
//   * loading    → "读取中…" — non-committal placeholder shown while
//                  we're still fetching the data that would let us
//                  make a real call (see AlgorithmNode.tsx for the
//                  gate). No Run button here — the operator doesn't
//                  yet know if the node needs running.
//   * never-ran  → "尚未运行 (never run)" + Run this node button.
//   * cleaned    → "产物已被清理 (artifact cleaned)" + Run this node
//                  button. Fires when the job ran once but its output
//                  was tombstoned via DELETE /api/artifacts/{id} — we
//                  detect this from HandleInfo.deleted_ts, so no HEAD
//                  probe or broken-media flash.
//
// The Run button reuses the existing V8 Continue-or-Fork dispatch
// endpoint (POST /api/workflows/{wid}/dispatch/{gnid}) — no new
// semantics invented here. Snapshot-canvas passes readOnly=true, which
// hides the button (running a node from a frozen past snapshot has
// no clean meaning; the user re-runs from the draft view).

import { useState } from "react";

export type PlaceholderKind = "never-ran" | "cleaned" | "loading";

export interface PreviewPlaceholderProps {
  kind: PlaceholderKind;
  packName: string;
  portName: string;
  // Called on Run click. Return a promise that resolves when dispatch
  // has been ACCEPTED (job_id assigned) — not when the run finishes.
  // The component flips to "waiting for run" locally on that resolve;
  // the real "running" indicator comes from runtime.state via
  // ``running`` below (job_update stream).
  onRun?: () => Promise<void>;
  // Live job state — when true the button shows "Running…" and is
  // disabled. Set by the parent from runtime?.state ∈
  // {pending, assigned, running}.
  running?: boolean;
  // When true, hide the Run button entirely (snapshot canvas: no
  // in-place re-run from a frozen view).
  readOnly?: boolean;
}

const SHELL: React.CSSProperties = {
  background: "var(--inverse-surface)",
  color: "var(--text-on-dark)",
  padding: "var(--space-3)",
  borderRadius: "var(--radius-md)",
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  justifyContent: "center",
  gap: 10,
  minHeight: 96,
  textAlign: "center",
  fontFamily: "var(--font-sans)",
};

const TITLE: React.CSSProperties = {
  fontSize: "var(--fs-sm)",
  fontWeight: 600,
  color: "var(--text-on-dark)",
};

const SUB: React.CSSProperties = {
  fontSize: "var(--fs-micro)",
  color: "var(--inverse-muted)",
  lineHeight: 1.4,
  maxWidth: 260,
};

const ERR: React.CSSProperties = {
  fontSize: "var(--fs-micro)",
  color: "var(--error)",
  lineHeight: 1.4,
  maxWidth: 260,
  wordBreak: "break-word",
};

const BUTTON_BASE: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  padding: "4px 12px",
  fontSize: "var(--fs-xs)",
  fontWeight: 600,
  border: "1px solid var(--inverse-muted, rgba(255,255,255,0.35))",
  borderRadius: "var(--radius-pill)",
  background: "transparent",
  color: "var(--text-on-dark)",
  cursor: "pointer",
  lineHeight: 1,
};

function content(kind: PlaceholderKind, portName: string): {
  title: string;
  detail: string;
} {
  if (kind === "loading") {
    return {
      title: "读取中…",
      detail: `正在读取 ${portName} 的产物信息。`,
    };
  }
  if (kind === "cleaned") {
    return {
      title: "产物已被清理",
      detail: `此节点的 ${portName} 产物已通过 Artifacts 面板清理，磁盘上文件已删除。重新运行本节点可再次生成产物。`,
    };
  }
  return {
    title: "尚未运行",
    detail: `此节点尚未运行过，还没有 ${portName} 产物可以预览。点击下方按钮触发单节点运行；上游产物必须已经在最新快照里就绪。`,
  };
}

export function PreviewPlaceholder({
  kind,
  packName,
  portName,
  onRun,
  running = false,
  readOnly = false,
}: PreviewPlaceholderProps) {
  const [dispatchState, setDispatchState] = useState<
    { kind: "idle" } | { kind: "sending" } | { kind: "err"; msg: string }
  >({ kind: "idle" });

  const { title, detail } = content(kind, portName);
  const buttonBusy = dispatchState.kind === "sending" || running;
  const buttonLabel = running
    ? "运行中…"
    : dispatchState.kind === "sending"
      ? "分派中…"
      : "▶ 运行本节点";
  // The "loading" kind is a non-committal placeholder — we don't yet
  // know if the node needs running, so suppress the Run button. The
  // real placeholder + button reappears once the fetch resolves.
  const showButton = kind !== "loading";

  const onClick = async () => {
    if (!onRun || buttonBusy) return;
    setDispatchState({ kind: "sending" });
    try {
      await onRun();
      setDispatchState({ kind: "idle" });
    } catch (err) {
      setDispatchState({
        kind: "err",
        msg: (err as Error).message || "dispatch failed",
      });
    }
  };

  return (
    <div
      data-hl-preview-placeholder={kind}
      data-hl-pack={packName}
      data-hl-port={portName}
      style={SHELL}
    >
      <div style={{ ...TITLE, color: kind === "loading" ? "var(--info)" : TITLE.color }}>{title}</div>
      <div style={SUB}>{detail}</div>
      {showButton && !readOnly && onRun && (
        <button
          type="button"
          onClick={onClick}
          disabled={buttonBusy}
          data-hl-run-node=""
          data-hl-running={running ? "1" : "0"}
          style={{
            ...BUTTON_BASE,
            cursor: buttonBusy ? "wait" : "pointer",
            opacity: buttonBusy ? 0.6 : 1,
          }}
        >
          {buttonLabel}
        </button>
      )}
      {dispatchState.kind === "err" && (
        <div style={ERR} data-hl-run-error="">
          分派失败: {dispatchState.msg}
        </div>
      )}
    </div>
  );
}
