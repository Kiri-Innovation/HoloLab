import { useEffect, useRef } from "react";
import { useViewerInteraction, ViewerInteractionShield, viewerInteractionBorder } from "./ViewerInteraction";

// The standalone viewer fetches only the browser export through the gateway
// proxy.  It never receives or reads torch checkpoints.
export function LagsplatPreview({ baseUrl }: { baseUrl: string }) {
  const ref = useRef<HTMLIFrameElement>(null);
  const {wrapperRef, activated, activate, onFrameLoad} = useViewerInteraction(true, ref);
  useEffect(() => {
    const frame = ref.current;
    if (!frame) return;
    const send = () => frame.contentWindow?.postMessage(
      { type: "lagsplat-model", url: `${baseUrl.replace(/\/$/, "")}/viewer_data/gaussians.js` },
      window.location.origin,
    );
    frame.addEventListener("load", send);
    return () => frame.removeEventListener("load", send);
  }, [baseUrl]);
  return (
    <div ref={wrapperRef} tabIndex={-1} data-viewer-interaction={activated ? "active" : "inactive"} style={{ position: "relative", height: 220, overflow: "hidden", outline: "none" }}>
      <iframe
        ref={ref}
        onLoad={onFrameLoad}
        title="LaGSplat Viewer"
        src="/lagsplat-viewer.html"
        scrolling="no"
        style={{ display: "block", width: "100%", height: "100%", boxSizing: "border-box", border: viewerInteractionBorder(activated), borderRadius: "var(--radius-sm)" }}
      />
      {!activated && <ViewerInteractionShield activate={activate} forwardCanvasDrag />}
    </div>
  );
}
