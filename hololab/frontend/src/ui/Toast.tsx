import { useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { CONTROL_STYLE } from "./controlStyles";

export interface ToastNotice {
  message: string;
  tone: "success" | "error";
  action?: { label: string; run: () => Promise<void> };
}

let current: (ToastNotice & { id: number }) | null = null;
let nextId = 0;
let timer: ReturnType<typeof setTimeout> | undefined;
const listeners = new Set<() => void>();
function emit() { listeners.forEach(listener => listener()); }
export function getToastSnapshot() { return current; }
export function subscribeToToast(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
export function dismissToast() {
  clearTimeout(timer);
  current = null;
  emit();
}

// A single global slot prevents a burst of copy actions from covering the UI.
// Replacing a notice restarts its timeout, including identical messages.
export function showToast(notice: ToastNotice) {
  clearTimeout(timer);
  current = { ...notice, id: ++nextId };
  timer = setTimeout(dismissToast, notice.tone === "error" ? 8000 : 3500);
  emit();
}

export function ToastViewport() {
  const notice = useSyncExternalStore(subscribeToToast, getToastSnapshot, () => null);
  const [busy, setBusy] = useState(false);
  return createPortal(
    <div
      aria-live="polite"
      aria-atomic="true"
      style={{ position: "fixed", bottom: 24, right: 24, zIndex: 10000, maxWidth: "min(380px, calc(100vw - 48px))", pointerEvents: "none" }}
    >
      {notice && (
        <div key={notice.id} role={notice.tone === "error" ? "alert" : "status"} style={{
          pointerEvents: "auto", padding: "12px 14px", background: "var(--surface)",
          color: "var(--text)", border: `1px solid var(--${notice.tone})`,
          borderRadius: "var(--radius-sm)", boxShadow: "var(--shadow-2)",
          fontSize: "var(--fs-sm)", overflowWrap: "anywhere",
        }}>
          <span>{notice.message}</span>
          {notice.action && (
            <button type="button" disabled={busy} style={{ ...CONTROL_STYLE, marginLeft: 8 }} onClick={async () => {
              setBusy(true);
              try { await notice.action!.run(); }
              finally { setBusy(false); }
            }}>{notice.action.label}</button>
          )}
          <button type="button" aria-label="关闭提示" style={{ ...CONTROL_STYLE, marginLeft: 8 }} onClick={dismissToast}>×</button>
        </div>
      )}
    </div>, document.body,
  );
}
