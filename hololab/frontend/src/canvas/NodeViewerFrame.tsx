import { useEffect, useRef, useState } from "react";
import type { NodeViewerSpec } from "../wire";
import type { WorkflowViewerView } from "../api";
import { getHandleSummary } from "../api";

export interface LiveViewerFrame {
  proxy_url: string;
  iteration: number | null;
  node_id: string | null;
}

// A pack viewer may request its natural content height via postMessage. Keep
// enough room for a useful diagnostic/dashboard while preventing a malformed
// or overly ambitious pack asset from making a canvas node unbounded.
const DEFAULT_VIEWER_HEIGHT = 336;
const MIN_VIEWER_HEIGHT = 160;
const MAX_VIEWER_HEIGHT = 520;

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
  workflowView,
  workflowViewLoaded,
  workflowViewManaged,
  onWorkflowDefaultChange,
}: {
  viewer: NodeViewerSpec;
  packName: string;
  packVersion: string;
  assignedNodeId: string | null;
  frames: LiveViewerFrame[];
  historyTarget?: { handle_id: string; proxy_url: string; node_id: string } | null;
  running: boolean;
  progress?: { current: number; total: number } | null;
  workflowView?: WorkflowViewerView | null;
  workflowViewLoaded: boolean;
  workflowViewManaged: boolean;
  onWorkflowDefaultChange?: (view: WorkflowViewerView | null) => void;
}) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [loaded, setLoaded] = useState(false);
  const [assetError, setAssetError] = useState<string | null>(null);
  const [historyFrames, setHistoryFrames] = useState<LiveViewerFrame[]>([]);
  const [viewerHeight, setViewerHeight] = useState(DEFAULT_VIEWER_HEIGHT);
  const sentWorkflowDefaultRef = useRef(false);
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
    setViewerHeight(DEFAULT_VIEWER_HEIGHT);
    sentWorkflowDefaultRef.current = false;
  }, [src]);

  useEffect(() => {
    if (!workflowViewLoaded) sentWorkflowDefaultRef.current = false;
  }, [workflowViewLoaded]);

  useEffect(() => {
    function onMessage(event: MessageEvent) {
      const iframe = iframeRef.current;
      if (
        !iframe
        || event.origin !== window.location.origin
        || event.source !== iframe.contentWindow
      ) return;
      if (event.data?.type === "hololab-viewer-camera-default") {
        onWorkflowDefaultChange?.(event.data.view && typeof event.data.view === "object" ? event.data.view : null);
        return;
      }
      if (event.data?.type !== "hololab-viewer-resize" || event.data?.viewer_id !== viewer.id) {
        return;
      }
      const requested = Number(event.data.height);
      if (!Number.isFinite(requested)) return;
      setViewerHeight(Math.min(MAX_VIEWER_HEIGHT, Math.max(MIN_VIEWER_HEIGHT, Math.ceil(requested))));
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [viewer.id, onWorkflowDefaultChange]);

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
              // Frame paths are relative to the index file itself. This
              // keeps an index plus its private images movable as a unit
              // inside a semantic output directory (e.g. state/previews/).
              proxy_url: `${base}/${historyIndex.split("/").slice(0, -1).concat(String(frame.path).split("/")).map(encodeURIComponent).join("/")}`,
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
        // A node-owned viewer can consume its declared history output even
        // when it is a scalar file rather than a frame sequence.
        history_url: historyTarget?.proxy_url ?? null,
        running,
        progress: progress ?? null,
      },
      window.location.origin,
    );
  }, [allFrames, loaded, running, progress?.current, progress?.total, viewer.id]);

  // A workflow default is an opening preference, not a live camera bus:
  // viewers already open must retain their own interactive orbit/zoom.
  useEffect(() => {
    const target = iframeRef.current?.contentWindow;
    if (!target || !loaded || !workflowViewLoaded || sentWorkflowDefaultRef.current) return;
    sentWorkflowDefaultRef.current = true;
    target.postMessage({
      type: "hololab-viewer-state",
      viewer_id: viewer.id,
      workflow_view: workflowView ?? null,
      workflow_view_managed: workflowViewManaged,
    }, window.location.origin);
  }, [loaded, workflowViewLoaded, workflowViewManaged, workflowView, viewer.id]);

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
      style={{ width: "100%", height: viewerHeight, border: 0, display: "block", background: "#101216" }}
    />
  );
}
