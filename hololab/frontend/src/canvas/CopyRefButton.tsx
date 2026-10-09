import { useEffect, useState } from "react";
import { showToast } from "../ui/Toast";
import { flopsInsertReferenceAvailable } from "../flops";
import { copyReferenceText, formatToken, sendReferences, type ReferenceInput, type ReferenceOutcome } from "./referenceAction";
export type { RefKind } from "./referenceAction";

// All outcomes use one global notice per operation/batch, even after unmount.
// Keep diagnostics out of node layout; a brief red icon identifies the source.
function showReferenceResult(result: ReferenceOutcome) {
  showToast({
    message: result.message,
    tone: result.state === "err" || result.insertionError ? "error" : "success",
    action: result.retryText ? {
      label: "重试复制",
      run: async () => {
        showReferenceResult(await copyReferenceText(result.retryText!, result.insertionError));
      },
    } : undefined,
  });
}

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
  const state = loading ? "loading" : outcome?.insertionError ? "err" : outcome?.state ?? "idle";
  const token = references.map(r => formatToken(r.kind, r.id, r.comment)).join("\n");
  // CoBrowser injects its API before page load. Recheck on each render (and
  // sendReferences checks again on click); no per-button polling is needed.
  const canInsert = flopsInsertReferenceAvailable();
  const actionLabel = canInsert ? "引用到 Flops" : "复制引用给 AI";
  const feedback = loading ? "正在处理引用…" : outcome?.message;


  useEffect(() => {
    if (!outcome) return;
    const timer = setTimeout(() => setOutcome(null), 2000);
    return () => clearTimeout(timer);
  }, [outcome]);

  const doReference = async (e: React.MouseEvent) => {
    e.stopPropagation();
    setLoading(true);
    setOutcome(null);
    try {
      const result = await sendReferences(references);
      showReferenceResult(result);
      setOutcome(result);
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
        data-tooltip={loading ? "正在处理引用" : actionLabel}
        aria-description={feedback ?? token}
        aria-label={`${actionLabel}${label ? `：${label}` : ""}`}
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
        {state === "loading" ? <span aria-hidden="true" style={{ width: canInsert ? 12 : 14, flexShrink: 0 }}>…</span>
          : state === "copied" || state === "inserted" ? <span aria-hidden="true" style={{ width: canInsert ? 12 : 14, flexShrink: 0 }}>✓</span>
          : state === "err" ? <span aria-hidden="true" style={{ width: canInsert ? 12 : 14, flexShrink: 0 }}>!</span>
          : (
            <svg
              width={canInsert ? 12 : 14}
              height={canInsert ? 12 : 14}
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={canInsert ? 2.2 : 2}
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
              focusable="false"
              data-reference-icon={canInsert ? "quote" : "clipboard"}
              style={{ flexShrink: 0 }}
            >
              {canInsert ? (
                // Paired quotation marks; match the adjacent source glyph's 12px / 2.2 stroke.
                <>
                  <path d="M10 6H3v7h4c0 2-1 3-3 4v2c4-1 6-4 6-8V6Z" />
                  <path d="M21 6h-7v7h4c0 2-1 3-3 4v2c4-1 6-4 6-8V6Z" />
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
    </span>
  );
}
