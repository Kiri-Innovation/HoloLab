import { useEffect, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import "./Toast.css";

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
export function dismissToast(id?: number) {
  // A stale close handler must not dismiss a newer replacement notice.
  if (id !== undefined && current?.id !== id) return;
  clearTimeout(timer);
  current = null;
  emit();
}

// A single global slot prevents a burst of copy actions from covering the UI.
// Replacing a notice restarts its timeout, including identical messages.
export function showToast(notice: ToastNotice) {
  clearTimeout(timer);
  current = { ...notice, id: ++nextId };
  const id = current.id;
  timer = setTimeout(() => dismissToast(id), notice.tone === "error" ? 8000 : 3500);
  emit();
}

// ReactFlow children outside its transformed viewport form a stable overlay.
// Register that host so the one global toast also works from toolbar/modals.
let canvasAnchor: HTMLDivElement | null = null;
const anchorListeners = new Set<() => void>();
function setCanvasAnchor(element: HTMLDivElement | null) {
  canvasAnchor = element;
  anchorListeners.forEach(listener => listener());
}
function subscribeToAnchor(listener: () => void) {
  anchorListeners.add(listener);
  return () => { anchorListeners.delete(listener); };
}
export function CanvasToastAnchor() {
  return <div ref={setCanvasAnchor} className="hl-canvas-toast-anchor" data-hl-toast-anchor="" />;
}

export function ToastViewport() {
  const notice = useSyncExternalStore(subscribeToToast, getToastSnapshot, () => null);
  const [busy, setBusy] = useState(false);
  const anchor = useSyncExternalStore(subscribeToAnchor, () => canvasAnchor, () => null);
  const [displayed, setDisplayed] = useState(notice);
  useEffect(() => {
    if (notice) {
      setDisplayed(notice);
      return;
    }
    // Match --dur-fast: retain the DOM only for the exit fade.
    const exitTimer = setTimeout(() => setDisplayed(null), 140);
    return () => clearTimeout(exitTimer);
  }, [notice]);
  return createPortal(
    <div
      aria-live="polite"
      aria-atomic="true"
      className={`hl-toast-viewport ${anchor ? "hl-toast-in-canvas" : "hl-toast-global"}`}
    >
      {displayed && (
        <div key={displayed.id} role={displayed.tone === "error" ? "alert" : "status"}
          className="hl-toast-pill" data-leaving={!notice}>
          <span>{displayed.message}</span>
          {displayed.action && (
            <button type="button" disabled={busy} className="hl-toast-action" onClick={async () => {
              setBusy(true);
              try { await displayed.action!.run(); }
              finally { setBusy(false); }
            }}>{displayed.action.label}</button>
          )}
          <button type="button" aria-label="关闭提示" title="关闭提示"
            className="hl-toast-action hl-toast-dismiss" onClick={() => dismissToast(displayed.id)}>
            <span aria-hidden="true">✓</span>
          </button>
        </div>
      )}
    </div>, anchor ?? document.body,
  );
}
