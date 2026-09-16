// Minimal modal — dark overlay + centered surface. Used for the "view full
// docs" flow on the Inspector so pack authors can write a paragraph without
// being penalised by the Inspector's clamped preview area.
//
// UX contract:
//   * Escape closes.
//   * Click outside the surface closes; click on the surface does not.
//   * Body scroll is not locked — the modal has its own scrollable body.
//   * Focus is not trapped: this is a passive reader, not a form dialog;
//     the underlying page stays accessible.

import { useEffect, type ReactNode } from "react";

export interface ModalProps {
  open: boolean;
  onClose: () => void;
  // Optional heading rendered in a small eyebrow row at the top-left of
  // the surface. The close affordance is rendered at top-right.
  title?: string;
  children: ReactNode;
  // Cap the surface width; defaults to a comfortable reading measure.
  maxWidth?: number;
}

export function Modal({ open, onClose, title, children, maxWidth = 720 }: ModalProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1000,
        background: "rgba(0, 0, 0, 0.55)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 24,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "var(--overlay)",
          color: "var(--text-body)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius-lg)",
          boxShadow: "var(--shadow-2)",
          width: "100%",
          maxWidth,
          maxHeight: "80vh",
          display: "flex",
          flexDirection: "column",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "10px 14px",
            borderBottom: "1px solid var(--border-subtle)",
            background: "var(--surface-2)",
            borderTopLeftRadius: "var(--radius-lg)",
            borderTopRightRadius: "var(--radius-lg)",
          }}
        >
          <span
            style={{
              fontSize: 10,
              fontWeight: 600,
              letterSpacing: "0.08em",
              textTransform: "uppercase",
              color: "var(--text-subtle)",
            }}
          >
            {title ?? ""}
          </span>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            title="Close (Esc)"
            style={{
              width: 24,
              height: 24,
              border: "none",
              background: "transparent",
              color: "var(--text-subtle)",
              fontSize: 18,
              lineHeight: 1,
              cursor: "pointer",
              borderRadius: "var(--radius-sm)",
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = "var(--surface-hover)";
              e.currentTarget.style.color = "var(--text)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = "transparent";
              e.currentTarget.style.color = "var(--text-subtle)";
            }}
          >
            ×
          </button>
        </div>
        <div style={{ padding: "14px 18px 18px", overflow: "auto", minHeight: 0 }}>
          {children}
        </div>
      </div>
    </div>
  );
}
