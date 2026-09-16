// Copy-a-hololab://reference button.
//
// The user's pain point: they want to point an agent at "this thing"
// (a run, a job, a handle) and have the agent resolve it unambiguously.
// This button emits a copy-paste token like:
//
//   hololab://job/7f6248ac-…  # stg-train · done · in run 2a444b3f
//
// The scheme + tail format is documented in
// hololab/gateway/refs.py and skill/SKILL.md ("resolving a reference").

import { useState } from "react";

export type RefKind =
  | "workflow"
  | "run"
  | "job"
  | "handle"
  | "pack"
  | "node"
  | "graph-node"
  // Index / collection kinds — they name a *page*, not a resource, so
  // they carry NO id. See ``hololab.gateway.refs._INDEX_KINDS``.
  | "workflows"
  | "artifacts";

export interface CopyRefButtonProps {
  kind: RefKind;
  // Empty string for index kinds (``workflows`` / ``artifacts``); a
  // resource id otherwise. Passing an id for an index kind causes the
  // resolver to reject the token, so callers should thread ``""``.
  id: string;
  // Optional human-readable trailing context — the parser strips it but
  // the human recognises it visually. Keep it short (algo · state · run
  // suffix). If omitted, only the bare token is copied.
  comment?: string;
  // Visual size preset. "sm" fits inside a compact toolbar / card
  // header (default); "xs" is for dense rows like RecentJobsPanel.
  size?: "sm" | "xs";
  // Optional label override. Defaults to just the icon.
  label?: string;
  // Optional theme flag for placement on dark surfaces (banner, preview
  // drawer). Adjusts colours so the button reads on either.
  onDark?: boolean;
}

function formatToken(kind: RefKind, id: string, comment?: string): string {
  // Index kinds render without the ``/id`` segment — the token is
  // ``hololab://workflows`` on its own. Every other kind carries the id.
  const base = id ? `hololab://${kind}/${id}` : `hololab://${kind}`;
  return comment ? `${base}  # ${comment}` : base;
}

async function writeToClipboard(text: string): Promise<boolean> {
  // navigator.clipboard requires a secure context; fall back to the
  // document.execCommand path so the button still works over plain http
  // (which is exactly how the LAN case runs).
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      /* fall through to the legacy path */
    }
  }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  document.body.removeChild(ta);
  return ok;
}

export function CopyRefButton({
  kind,
  id,
  comment,
  size = "sm",
  label,
  onDark = false,
}: CopyRefButtonProps) {
  const [state, setState] = useState<"idle" | "copied" | "err">("idle");
  const token = formatToken(kind, id, comment);

  const doCopy = async (e: React.MouseEvent) => {
    e.stopPropagation();
    const ok = await writeToClipboard(token);
    setState(ok ? "copied" : "err");
    window.setTimeout(() => setState("idle"), 1500);
  };

  // The visual variants differ by context, not by a one-off hit target.
  // Both use the compact control scale.
  const dims = size === "xs"
    ? { w: "var(--control-h-sm)", h: "var(--control-h-sm)", font: "var(--fs-sm)" }
    : { w: "var(--control-h-sm)", h: "var(--control-h-sm)", font: "var(--fs-sm)" };

  const bg = onDark ? "transparent" : "var(--surface)";
  const border = onDark
    ? "1px solid var(--inverse-border)"
    : "1px solid var(--border)";
  const color =
    state === "copied"
      ? "var(--success)"
      : state === "err"
        ? "var(--error)"
        : onDark
          ? "var(--inverse-muted)"
          : "var(--text-muted)";

  return (
    <button
      type="button"
      onClick={doCopy}
      title={
        state === "copied"
          ? "copied — paste into a chat with your agent"
          : state === "err"
            ? "copy failed — select the terminal token and copy manually"
            : `copy reference: ${token}`
      }
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 4,
        width: label ? undefined : dims.w,
        height: dims.h,
        padding: label ? "0 6px" : 0,
        borderRadius: "var(--radius-sm)",
        border,
        background: bg,
        color,
        cursor: "pointer",
        fontSize: dims.font,
        lineHeight: 1,
        transition:
          "background var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease)",
      }}
    >
      {state === "copied" ? "✓" : state === "err" ? "!" : "⧉"}
      {label && <span>{label}</span>}
    </button>
  );
}
