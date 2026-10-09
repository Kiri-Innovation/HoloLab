import { useState } from "react";
import { flopsInsertReferenceAvailable } from "../flops";
import { copyReferenceText, formatToken, sendReferences, type ReferenceInput, type ReferenceOutcome } from "./referenceAction";
export type { RefKind } from "./referenceAction";

interface ButtonOptions {
  disabled?: boolean;
  // Visual size preset. "sm" fits inside a compact toolbar / card
  // header (default); "xs" is for dense rows like RecentJobsPanel.
  size?: "sm" | "xs";
  // Optional label override. Defaults to just the icon.
  label?: string;
  // Optional theme flag for placement on dark surfaces (banner, preview
  // drawer). Adjusts colours so the button reads on either.
  onDark?: boolean;
}

export type CopyRefButtonProps = ButtonOptions & (ReferenceInput | { references: ReferenceInput[] });
export function CopyRefButton(props: CopyRefButtonProps) {
  const { size = "sm", label, onDark = false, disabled = false } = props;
  const references = "references" in props ? props.references : [props];
  const [outcome, setOutcome] = useState<ReferenceOutcome | null>(null);
  const [loading, setLoading] = useState(false);
  const state = loading ? "loading" : outcome?.state ?? "idle";
  const token = references.map(r => formatToken(r.kind, r.id, r.comment)).join("\n");
  // CoBrowser injects its API before page load. Recheck on each render (and
  // sendReferences checks again on click); no per-button polling is needed.
  const canInsert = flopsInsertReferenceAvailable();
  const actionLabel = canInsert ? "引用到 Flops" : "复制引用";
  const feedback = loading ? "正在处理引用…" : outcome?.message;


  const doReference = async (e: React.MouseEvent) => {
    e.stopPropagation();
    setLoading(true);
    setOutcome(null);
    try {
      setOutcome(await sendReferences(references));
    } finally {
      setLoading(false);
    }
  };

  const retryCopy = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!outcome?.retryText) return;
    setLoading(true);
    try {
      setOutcome(await copyReferenceText(outcome.retryText, outcome.retryPrefix));
    } finally {
      setLoading(false);
    }
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
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <button
        type="button"
        onClick={doReference}
        disabled={disabled || loading || references.length === 0}
        title={feedback ?? `${actionLabel}: ${token}`}
        aria-label={feedback ?? `${actionLabel}${label ? `：${label}` : ""}`}
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
        {state === "loading" ? <span aria-hidden="true">…</span>
          : state === "copied" || state === "inserted" ? <span aria-hidden="true">✓</span>
          : state === "err" ? <span aria-hidden="true">!</span>
          : (
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
              focusable="false"
              data-reference-icon={canInsert ? "reference" : "clipboard"}
              style={{ flexShrink: 0 }}
            >
              {canInsert ? (
                // Reuse the link glyph from the former InsertAiReferenceButton.
                <>
                  <path d="M10 13a5 5 0 0 0 7.07.07l2-2a5 5 0 0 0-7.07-7.07l-1.15 1.15" />
                  <path d="M14 11a5 5 0 0 0-7.07-.07l-2 2A5 5 0 0 0 12 20l1.15-1.15" />
                </>
              ) : (
                <>
                  <rect x="9" y="3" width="6" height="4" rx="1" />
                  <path d="M9 5H6a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-3" />
                  <path d="M8 11h8M8 15h8" />
                </>
              )}
            </svg>
          )}
        {label && <span>{label}</span>}
      </button>
      {outcome && (
        <span role="status" style={{ maxWidth: 280, whiteSpace: "normal", overflowWrap: "anywhere", fontSize: "var(--fs-xs)", color }}>
          {outcome.message}
          {outcome.retryText && <button type="button" disabled={loading || disabled} onClick={retryCopy}>重试复制</button>}
        </span>
      )}
    </span>
  );
}
