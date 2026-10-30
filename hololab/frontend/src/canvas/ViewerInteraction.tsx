import { useEffect, useRef, useState, type RefObject, type MouseEvent as ReactMouseEvent } from "react";

const ACTIVATED = "hololab-viewer-interaction-activated";

/** Host-side input gate shared by COLMAP and SplaTV. The first click only
 * unlocks the iframe; subsequent canvas clicks focus its camera controller.
 * Iframe wheel events do not bubble to the host, so intercept them BEFORE
 * they enter the iframe rather than changing the renderer's controls. */
export function useViewerInteraction(enabled = true, iframeRef?: RefObject<HTMLIFrameElement>) {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const [activated, setActivated] = useState(false);
  const frameLoad = useRef<() => void>(() => {});
  useEffect(() => {
    if (!enabled) return;
    // Capture is required: xyflow/d3 stops bubbling mousedown events.
    const onDown = (event: MouseEvent) => {
      if (!wrapperRef.current?.contains(event.target as Node)) setActivated(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setActivated(false);
    };
    const onOther = (event: Event) => {
      if ((event as CustomEvent).detail !== wrapperRef.current) setActivated(false);
    };
    document.addEventListener("mousedown", onDown, true);
    document.addEventListener("keydown", onKey);
    window.addEventListener(ACTIVATED, onOther);
    return () => {
      document.removeEventListener("mousedown", onDown, true);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener(ACTIVATED, onOther);
    };
  }, [enabled]);
  // Optional host gate for ordinary same-origin pack frames. Existing COLMAP /
  // SplaTV focus protocols keep their own keyboard handling unchanged.
  useEffect(() => {
    if (!enabled || !iframeRef) return;
    let inner: Document | null = null;
    const deactivate = () => {
      setActivated(false);
      if (document.activeElement === iframeRef.current) wrapperRef.current?.focus({preventScroll: true});
    };
    const key = (event: KeyboardEvent) => { if (event.key === "Escape") deactivate(); };
    const visibility = () => { if (document.hidden) deactivate(); };
    const attach = () => {
      inner?.removeEventListener("keydown", key, true);
      // Pack assets are same-origin; tolerate a viewer navigating elsewhere.
      try { inner = iframeRef.current?.contentDocument ?? null; } catch { inner = null; }
      inner?.addEventListener("keydown", key, true);
      setActivated(false);
    };
    attach();
    frameLoad.current = attach;
    document.addEventListener("visibilitychange", visibility);
    return () => {
      inner?.removeEventListener("keydown", key, true);
      frameLoad.current = () => {};
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [enabled, iframeRef]);
  const activate = () => {
    window.dispatchEvent(new CustomEvent(ACTIVATED, { detail: wrapperRef.current }));
    setActivated(true);
  };
  return { wrapperRef, activated, activate, onFrameLoad: () => frameLoad.current() };
}

export function viewerInteractionBorder(activated: boolean) {
  return activated ? "1.5px solid var(--accent)" : "1px solid var(--border-strong)";
}

export function ViewerInteractionShield({ activate, forwardCanvasDrag = false }: {
  activate: (event: ReactMouseEvent<HTMLDivElement>) => void; forwardCanvasDrag?: boolean;
}) {
  const start = useRef<{x: number; y: number} | null>(null);
  return <div onPointerDown={event => { if (forwardCanvasDrag) event.stopPropagation(); }} onMouseDown={event => {
    if (!forwardCanvasDrag || event.button !== 0) return;
    start.current = {x: event.clientX, y: event.clientY};
    const pane = event.currentTarget.closest(".react-flow")?.querySelector(".react-flow__pane");
    if (!pane) return;
    event.stopPropagation();
    // Let the existing canvas pan controller own the gesture, including its
    // movement threshold; do not invent another viewport/drag implementation.
    pane.dispatchEvent(new MouseEvent("mousedown", {bubbles: true, cancelable: true,
      view: window, button: event.button, buttons: event.buttons,
      clientX: event.clientX, clientY: event.clientY}));
  }} onClick={event => {
    if (forwardCanvasDrag && start.current && Math.hypot(event.clientX-start.current.x, event.clientY-start.current.y) > 3) return;
    activate(event);
  }} style={{
    position: "absolute", inset: 0, zIndex: 10,
    cursor: "default", background: "transparent",
  }} />;
}
