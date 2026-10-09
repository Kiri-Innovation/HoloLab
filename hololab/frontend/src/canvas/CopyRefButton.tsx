// HoloLab reference button: insert into Flops when available, otherwise copy.
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
import {
  flopsInsertReference,
  flopsInsertReferenceAvailable,
  type FlopsInsertReferenceParams,
} from "../flops";
import { referenceSnapshotDocument, resolveReference } from "./referenceSnapshot";

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
  /** Human-readable labels for the CoBrowser reference pill. */
  referenceDisplay?: { title: string; subtitle: string };
}

function formatToken(kind: RefKind, id: string, comment?: string): string {
  // Index kinds render without the ``/id`` segment — the token is
  // ``hololab://workflows`` on its own. Every other kind carries the id.
  const base = id ? `hololab://${kind}/${id}` : `hololab://${kind}`;
  return comment ? `${base}  # ${comment}` : base;
}

function truncateSnapshot(text: string): string {
  const limit = 60 * 1024;
  const bytes = new TextEncoder().encode(text);
  if (bytes.length <= limit) return text;
  return `${new TextDecoder().decode(bytes.slice(0, limit - 32))}\n\n_摘要已截断。_`;
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
  referenceDisplay,
}: CopyRefButtonProps) {
  const [state, setState] = useState<"idle" | "loading" | "copied" | "inserted" | "err">("idle");
  const token = formatToken(kind, id, comment);

  const doReference = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (flopsInsertReferenceAvailable()) {
      setState("loading");
      const path = kind === "workflow" ? `w/${id}` : id ? `${kind}/${id}` : kind;
      // The resolver DTO is the one snapshot source for every UI anchor.
      // If the gateway is temporarily unavailable, retain a useful, clearly
      // limited reference rather than silently claiming a full resource view.
      let snapshotText: string;
      try {
        snapshotText = referenceSnapshotDocument(await resolveReference(token));
      } catch {
        snapshotText = referenceSnapshotDocument({
          kind,
          ref: token,
          resource: { resolver_status: "unavailable" },
          related: {},
        });
      }
      const payload: FlopsInsertReferenceParams = {
        schemaVersion: 1,
        provider: "hololab",
        type: kind === "graph-node" ? "workflow_node" : kind.replace(/-/g, "_"),
        // This is the actual running instance, never the old marketing host.
        resource: { uri: new URL(`/${path}`, window.location.origin).toString() },
        display: {
          title: referenceDisplay?.title ?? `HoloLab ${kind}`,
          subtitle: referenceDisplay?.subtitle ?? comment ?? token,
          icon: kind === "graph-node" || kind === "workflow" ? "workflow" : "external",
        },
        access: { mode: "link_only" },
        snapshot: {
          text: truncateSnapshot(snapshotText),
          mediaType: "text/markdown",
        },
      };
      try {
        const result = await flopsInsertReference(payload);
        setState(result.success ? "inserted" : "err");
      } catch {
        setState("err");
      }
      window.setTimeout(() => setState("idle"), 2000);
      return;
    }
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
    state === "copied" || state === "inserted"
      ? "var(--success)"
      : state === "err"
        ? "var(--error)"
        : onDark
          ? "var(--inverse-muted)"
          : "var(--text-muted)";

  return (
    <button
      type="button"
      onClick={doReference}
      disabled={state === "loading"}
      title={
        state === "loading"
          ? "正在插入引用…"
          : state === "inserted"
          ? "已插入 Flops 输入框"
          : state === "copied"
          ? "已复制 — 粘贴到与 agent 的对话中"
          : state === "err"
            ? "引用失败"
            : flopsInsertReferenceAvailable()
              ? `插入引用到 Flops: ${token}`
              : `复制引用: ${token}`
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
        cursor: state === "loading" ? "wait" : "pointer",
        fontSize: dims.font,
        lineHeight: 1,
        transition:
          "background var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease)",
      }}
    >
      {state === "loading" ? "…" : state === "copied" || state === "inserted" ? "✓" : state === "err" ? "!" : "⧉"}
      {label && <span>{label}</span>}
    </button>
  );
}
