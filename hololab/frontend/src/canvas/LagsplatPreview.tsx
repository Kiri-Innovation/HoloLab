import { useEffect, useRef } from "react";

// The standalone viewer fetches only the browser export through the gateway
// proxy.  It never receives or reads torch checkpoints.
export function LagsplatPreview({ baseUrl }: { baseUrl: string }) {
  const ref = useRef<HTMLIFrameElement>(null);
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
    <div style={{ height: 220, overflow: "hidden" }}>
      <iframe
        ref={ref}
        title="LaGSplat Viewer"
        src="/lagsplat-viewer.html"
        scrolling="no"
        style={{ display: "block", width: "100%", height: "100%", border: 0 }}
      />
    </div>
  );
}
