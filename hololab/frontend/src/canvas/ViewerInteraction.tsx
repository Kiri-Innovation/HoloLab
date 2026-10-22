import { useEffect, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";

const ACTIVATED = "hololab-viewer-interaction-activated";

/** Host-side input gate shared by COLMAP and SplaTV. The first click only
 * unlocks the iframe; subsequent canvas clicks focus its camera controller.
 * Iframe wheel events do not bubble to the host, so intercept them BEFORE
 * they enter the iframe rather than changing the renderer's controls. */
export function useViewerInteraction(enabled = true) {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const [activated, setActivated] = useState(false);
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
  const activate = () => {
    window.dispatchEvent(new CustomEvent(ACTIVATED, { detail: wrapperRef.current }));
    setActivated(true);
  };
  return { wrapperRef, activated, activate };
}

export function viewerInteractionBorder(activated: boolean) {
  return activated ? "1.5px solid var(--accent)" : "1px solid var(--border-strong)";
}

export function ViewerInteractionShield({ activate }: { activate: (event: ReactMouseEvent<HTMLDivElement>) => void }) {
  return <div onClick={activate} style={{
    position: "absolute", inset: 0, zIndex: 10,
    cursor: "default", background: "transparent",
  }} />;
}
