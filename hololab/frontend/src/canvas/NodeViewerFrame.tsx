import { useEffect, useRef, useState } from "react";
import type { NodeViewerSpec } from "../wire";
import { getHandleSummary } from "../api";

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
  historyTarget,
  running,
  progress,
}: {
  viewer: NodeViewerSpec;
  packName: string;
  packVersion: string;
  assignedNodeId: string | null;
  frames: LiveViewerFrame[];
  historyTarget?: { handle_id: string; proxy_url: string; node_id: string } | null;
  running: boolean;
  progress?: { current: number; total: number } | null;
}) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [loaded, setLoaded] = useState(false);
  const [assetError, setAssetError] = useState<string | null>(null);
  const [historyFrames, setHistoryFrames] = useState<LiveViewerFrame[]>([]);
  const nodeId = (frames.length ? frames[frames.length - 1].node_id : null)
    ?? historyTarget?.node_id
    ?? assignedNodeId;
  const src = nodeId
    ? `/pack-assets/${encodeURIComponent(nodeId)}/${encodeURIComponent(packName)}/${encodeURIComponent(packVersion)}/${viewer.entry.split("/").map(encodeURIComponent).join("/")}`
    : null;

  useEffect(() => {
    if (!src) return;
    const controller = new AbortController();
    setLoaded(false);
    setAssetError(null);
    void fetch(src, { signal: controller.signal, cache: "no-store" })
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
      })
      .catch((error) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setAssetError(`viewer 资源不可用（${String(error instanceof Error ? error.message : error)}）`);
      });
    return () => controller.abort();
  }, [src]);

  useEffect(() => {
    if (!historyTarget) {
      setHistoryFrames([]);
      return;
    }
    let cancelled = false;
    const base = historyTarget.proxy_url.replace(/\/$/, "");
    // Nodes predating the ``history_index`` schema field ignore it until
    // their next restart. ``state/previews/index.json`` is the STG private
    // diagnostics convention, retained as a backwards-compatible fallback.
    const historyIndex = viewer.history_index
      ?? (viewer.history_output === "state" ? "previews/index.json" : null);
    const restore = historyIndex
      ? fetch(`${base}/${historyIndex.split("/").map(encodeURIComponent).join("/")}`, {
        cache: "no-store",
      })
        .then(async (response) => {
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const body = await response.json() as {
            frames?: Array<{ iteration?: unknown; path?: unknown }>;
          };
          return (body.frames ?? [])
            .filter((frame) => typeof frame.path === "string" && !frame.path.startsWith("/") && !frame.path.split("/").includes(".."))
            .map((frame) => ({
              proxy_url: `${base}/${String(frame.path).split("/").map(encodeURIComponent).join("/")}`,
              iteration: typeof frame.iteration === "number" ? frame.iteration : null,
              node_id: null,
            }));
        })
      : getHandleSummary(historyTarget.handle_id)
        .then((summary) => (summary.fields.entries ?? [])
          .filter((entry) => !entry.is_dir && /\.png$/i.test(entry.name))
          .map((entry) => {
            const match = /iter[_]?(\d+)/.exec(entry.name);
            return {
              proxy_url: `${base}/${entry.name}`,
              iteration: match ? Number(match[1]) : null,
              node_id: null,
            };
          }));
    void restore
      .then((restored) => {
        if (cancelled) return;
        setHistoryFrames(restored);
      })
      .catch(() => {
        if (!cancelled) setHistoryFrames([]);
      });
    return () => { cancelled = true; };
  }, [historyTarget?.handle_id, historyTarget?.proxy_url, viewer.history_index, viewer.history_output]);

  const allFrames = [...historyFrames, ...frames]
    .reduce<LiveViewerFrame[]>((out, frame) => {
      const index = out.findIndex((old) => frame.iteration != null
        ? old.iteration === frame.iteration
        : old.proxy_url === frame.proxy_url);
      if (index >= 0) out[index] = frame;
      else out.push(frame);
      return out;
    }, [])
    .sort((a, b) => (a.iteration ?? -1) - (b.iteration ?? -1));

  useEffect(() => {
    const target = iframeRef.current?.contentWindow;
    if (!target || !loaded) return;
    target.postMessage(
      {
        type: "hololab-viewer-state",
        viewer_id: viewer.id,
        frames: allFrames,
        running,
        progress: progress ?? null,
      },
      window.location.origin,
    );
  }, [allFrames, loaded, running, progress?.current, progress?.total, viewer.id]);

  if (!src) {
    return (
      <div style={{ padding: 12, color: "var(--inverse-muted)", fontSize: 11 }}>
        {running ? "等待训练节点分配…" : "尚未运行"}
      </div>
    );
  }
  if (assetError) {
    return <div style={{ padding: 12, color: "var(--status-failed)", fontSize: 11 }}>{assetError}</div>;
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
