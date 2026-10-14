import { useEffect, useRef, useState } from "react";
import type { NodeViewerSpec } from "../wire";

export interface LiveViewerFrame {
  proxy_url: string;
  iteration: number | null;
  node_id: string | null;
}

/** A pack-owned iframe. It is a style/isolation boundary, not a sandbox for
 * mutually-hostile code: packs execute on the trusted developer node. */
export function NodeViewerFrame({
  viewer,
  packName,
  packVersion,
  assignedNodeId,
  frames,
  running,
}: {
  viewer: NodeViewerSpec;
  packName: string;
  packVersion: string;
  assignedNodeId: string | null;
  frames: LiveViewerFrame[];
  running: boolean;
}) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [loaded, setLoaded] = useState(false);
  const nodeId = (frames.length ? frames[frames.length - 1].node_id : null) ?? assignedNodeId;
  const src = nodeId
    ? `/pack-assets/${encodeURIComponent(nodeId)}/${encodeURIComponent(packName)}/${encodeURIComponent(packVersion)}/${viewer.entry.split("/").map(encodeURIComponent).join("/")}`
    : null;

  useEffect(() => {
    const target = iframeRef.current?.contentWindow;
    if (!target || !loaded) return;
    target.postMessage(
      {
        type: "hololab-viewer-state",
        viewer_id: viewer.id,
        frames,
        running,
      },
      window.location.origin,
    );
  }, [frames, loaded, running, viewer.id]);

  if (!src) {
    return (
      <div style={{ padding: 12, color: "var(--inverse-muted)", fontSize: 11 }}>
        {running ? "等待训练节点分配…" : "尚未运行"}
      </div>
    );
  }
  return (
    <iframe
      ref={iframeRef}
      src={src}
      title={viewer.title ?? viewer.id}
      sandbox="allow-scripts allow-same-origin"
      onLoad={() => setLoaded(true)}
      style={{ width: "100%", height: 336, border: 0, display: "block", background: "#101216" }}
    />
  );
}
