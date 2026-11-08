import { packAssetUrl } from "../packageIdentity";
// Shared preview policy and composition. Low-level viewers remain unchanged.
import { useEffect, useMemo, useState } from "react";
import type { CatalogPack, OutputPortSpec } from "../wire";
import type { AlgorithmNodeData, PreviewTarget } from "./AlgorithmNode";
import { effectivePortArrayed, effectivePortDimLabels } from "../tags";
import { useCanvasContext } from "./CanvasContext";
import { CopyRefButton } from "./CopyRefButton";
import { OpenInCocoderButton } from "./OpenInCocoderButton";
import { BasicInfoPreview, Preview } from "./previews";
import { PreviewLoadingSkeleton, PreviewPlaceholder } from "./PreviewPlaceholder";
import { parseRuntimeObservation } from "../types/runtimeObservation";
import { observedValue } from "../types/renderPortType";
import { NodeViewerFrame } from "./NodeViewerFrame";

// An expandable output = a port the operator can open the drawer on.
// Two cases:
//
//   * ``kind: "viewer"`` — the pack declared a preview viewer. The
//     caret shows even without a live handle so the "尚未运行 / 产物已被清理"
//     placeholders (see PreviewPlaceholder) + the in-place Run button
//     still work.
//   * ``kind: "info"``  — no preview declared but the last run resolved
//     a handle. The drawer shows BasicInfoPreview (path, size, contents,
//     Open-in-Cocoder) so operators aren't blind to what nodes without
//     a bespoke viewer (stg-train, generic regrouping, colmap-assemble, …)
//     just produced.
//
// A port with both a preview spec AND a target keeps ``kind: "viewer"``
// — the viewer's own header already carries the same Open-in-Cocoder
// + CopyRef affordances BasicInfoPreview surfaces.
type ExpandKind = "viewer" | "info";
export interface ExpandablePort {
  name: string;
  spec: OutputPortSpec;
  target: PreviewTarget | null;
  kind: ExpandKind;
}

function expandableOutputs(
  pack: CatalogPack,
  previews: Record<string, PreviewTarget> | undefined,
): ExpandablePort[] {
  // Prefer an actual viewer over a plain artifact inspection panel.  Output
  // declaration order is a wiring/detail concern, not a visual-priority
  // contract: a training state commonly comes first so it can be connected
  // downstream, while an image/video diagnostic should be the node's default
  // drawer.  Keep declaration order within each tier for deterministic UI.
  const viewers: ExpandablePort[] = [];
  const infos: ExpandablePort[] = [];
  for (const [name, spec] of Object.entries(pack.outputs)) {
    const target = previews?.[name] ?? null;
    // Backend type declarations own both static and runtime viewer selection.
    const staticViewer = spec.preview;
    const runtimeViewer = Boolean(target?.preview);
    if (staticViewer || runtimeViewer) {
      viewers.push({ name, spec, target, kind: "viewer" });
    } else if (target) {
      infos.push({ name, spec, target, kind: "info" });
    } else {
      // No preview spec and no handle yet — include anyway so the caret
      // is always visible for nodes with outputs, and clicking it shows
      // the "尚未运行" placeholder + Run button before the first run.
      infos.push({ name, spec, target: null, kind: "info" });
    }
  }
  return [...viewers, ...infos];
}

export function useNodePreviewSelection(data: AlgorithmNodeData, defaultOpen = false) {
  const { pack, previews, assigned_node_id, nodeViewerFrames, previewOpen } = data;
  const defaultNodeViewer =
    pack.node_viewers?.find((viewer) => viewer.default) ?? pack.node_viewers?.[0] ?? null;
  // A long-running gateway can encounter a freshly-authored manifest before
  // its Python schema knows a new catalog field. In that rolling-upgrade
  // window, read just this declarative boolean from the already trusted,
  // same-version pack asset. Once the gateway serializes the field, this
  // effect is a no-op and the catalog remains the source of truth.
  const [compatHideDefaultOutputPreviews, setCompatHideDefaultOutputPreviews] = useState(false);
  useEffect(() => {
    if (typeof pack.hide_default_output_previews === "boolean" || !defaultNodeViewer || !assigned_node_id) {
      setCompatHideDefaultOutputPreviews(false);
      return;
    }
    let cancelled = false;
    const path = packAssetUrl(assigned_node_id, pack.name, pack.version, "manifest.yaml");
    void fetch(path, { cache: "no-store" })
      .then((response) => response.ok ? response.text() : "")
      .then((text) => {
        if (!cancelled) {
          setCompatHideDefaultOutputPreviews(/^hide_default_output_previews:\s*true\s*(?:#.*)?$/m.test(text));
        }
      })
      .catch(() => { if (!cancelled) setCompatHideDefaultOutputPreviews(false); });
    return () => { cancelled = true; };
  }, [assigned_node_id, defaultNodeViewer?.id, pack.hide_default_output_previews, pack.name, pack.version]);
  // A pack may opt into a node-owned viewer as its whole card-bottom UI.
  // Do not infer this from the presence of a node viewer: coexistence with
  // ordinary output inspection remains the default for every existing pack.
  const hideDefaultOutputPreviews = pack.hide_default_output_previews === true
    || compatHideDefaultOutputPreviews;
  const expandables = hideDefaultOutputPreviews ? [] : expandableOutputs(pack, previews);
  const defaultNodeViewerFrames = defaultNodeViewer
    ? nodeViewerFrames?.[defaultNodeViewer.id] ?? []
    : [];
  // Expand a port so long as it is expandable (viewer-backed OR a
  // handle exists). The drawer decides at render time whether to show
  // the real Preview, the basic-info panel, or a placeholder.
  const expandableNames = useMemo(
    () => new Set(expandables.map((p) => p.name)),
    [expandables],
  );
  const requestedPreview =
    previewOpen && expandableNames.has(previewOpen)
      ? expandables.find((p) => p.name === previewOpen) ?? null
      : null;
  const preferredPreview = expandables[0] ?? null;
  // `preview_open` is persisted on workflow nodes.  Before viewer-first
  // ordering, opening a multi-output node saved its first (often directory)
  // output here.  Promote that stale info selection to the first live viewer
  // so existing workflows immediately show their diagnostic image/grid.
  const currentPreview =
    requestedPreview?.kind === "info" && preferredPreview?.kind === "viewer"
      ? preferredPreview
      : requestedPreview ?? (defaultOpen ? preferredPreview : null);
  return { defaultNodeViewer, defaultNodeViewerFrames, expandables, currentPreview };
}

export function NodePreviews({ data, selection, onRun, inspector = false }: {
  data: AlgorithmNodeData;
  selection: ReturnType<typeof useNodePreviewSelection>;
  onRun?: () => Promise<void>;
  inspector?: boolean;
}) {
  const { pack, previews, outputHandleIds, previewShards, previewLoadingPorts, previewFactsPendingPorts, previewFactsFailedPorts, previewFacts, previewLoadErrors, previewRefreshToken = 0, runtime, arrayed_toggle, assigned_node_id, readOnly } = data;
  const {
    computeNodesById,
    hydrating,
    viewer_view,
    viewer_view_loaded,
    viewer_view_managed,
    setViewerDefault,
  } = useCanvasContext();
  const { currentPreview, defaultNodeViewer, defaultNodeViewerFrames } = selection;
  const expanded = currentPreview?.name ?? null;
  const runState = runtime?.state;
  const runInFlight = runState === "pending" || runState === "assigned" || runState === "running";
  // Outputs are materialised before the subprocess starts, but are only
  // registered as artifact handles at completion.  Keep the running output
  // addressable through the node file proxy so a page reload can restore a
  // pack-owned viewer before that terminal handle exists.
  const runningHistoryTarget = defaultNodeViewer?.history_output && runInFlight
    && runtime?.node_id && runtime?.workflow_id && runtime?.job_id
    ? {
      handle_id: `running:${runtime.job_id}:${defaultNodeViewer.history_output}`,
      node_id: runtime.node_id,
      proxy_url: `/proxy/${encodeURIComponent(runtime.node_id)}/w/${encodeURIComponent(runtime.workflow_id)}/j/${encodeURIComponent(runtime.job_id)}/${encodeURIComponent(defaultNodeViewer.history_output)}`,
    }
    : null;
  return <>
      {inspector && !currentPreview && !defaultNodeViewer && <p className="hl-preview-empty">No preview output is available for this node.</p>}
      {expanded && currentPreview && (
        <div
          // ``nodrag``: keep pointer gestures inside the drawer local —
          // clicking a video tile, dragging the shared playback bar's
          // scrubber, etc. must not double-fire as a node-move. Any
          // preview element that OWNS its own drag (the scrubber
          // specifically) also carries its own ``nodrag`` for
          // defence-in-depth.
          //
          // Wheel follows the canvas navigation mode: pan in trackpad
          // mode, zoom in mouse mode. Embedded viewers opt out through
          // their explicit ``data-wheel-owner="local"`` containers.
          className="nodrag hl-preview-drawer"
          style={{
            padding: 8,
            borderTop: "1px solid var(--border)",
            background: "var(--inverse-surface)",
            borderRadius: "0 0 var(--radius-md) var(--radius-md)",
          }}
        >
          {(() => {
            const { name, spec: port, target, kind: expandKind } = currentPreview;
            // Live drawer body when the handle is present and not
            // tombstoned. Both viewer and basic-info kinds share the
            // same header (pack · port + Open-in-Cocoder + CopyRef) so
            // the operator gets the same actions regardless of whether
            // the pack shipped a viewer. Only the body under the header
            // differs: real Preview vs BasicInfoPreview.
            if (inspector && expandKind === "info" && target && !target.deleted) {
              return <p className="hl-preview-empty">No visualization is available for this artifact.</p>;
            }
            const showLive = target !== null && !target.deleted;
            if (showLive && target) {
              const header = (
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    marginBottom: 6,
                    fontSize: 10,
                    color: "var(--inverse-muted)",
                  }}
                >
                  <span style={{ flex: 1 }}>
                    {pack.name} · {name}
                  </span>
                  {previewFactsPendingPorts?.[name] && (
                    <span title="正在读取已完成产物的类型和维度；预览文件仍会自动加载" style={{ fontSize: 9, color: "var(--info)", whiteSpace: "nowrap" }}>
                      尺寸计算中…
                    </span>
                  )}
                  {previewFactsFailedPorts?.[name] && (
                    <span title={previewFactsFailedPorts[name]} style={{ fontSize: 9, color: "var(--warning)", whiteSpace: "nowrap" }}>
                      信息不可用
                    </span>
                  )}
                  <OpenInCocoderButton
                    path={target.absolute_path}
                    computeNode={
                      computeNodesById?.[target.node_id] ?? null
                    }
                    onDark
                  />
                  <CopyRefButton
                    kind="handle"
                    id={target.handle_id}
                    comment={`${pack.name} · ${name} output`}
                    size="xs"
                    onDark
                  />
                </div>
              );
              if (expandKind === "viewer") {
                return (
                  <>
                    {header}
                    <Preview
                      key={`${target.handle_id}:${previewRefreshToken}`}
                      // Runtime type resolution takes precedence over catalog defaults.
                      spec={target.preview ?? port.preview ?? undefined}
                      baseUrl={target.proxy_url}
                      storage={target.storage}
                      handleId={target.handle_id}
                      absolutePath={target.absolute_path}
                      producingNode={
                        computeNodesById?.[target.node_id] ?? null
                      }
                      // Tags are diagnostic context; renderer policy belongs to the backend.
                      tags={target.tags.length > 0 ? target.tags : port.tags}
                      arrayed={effectivePortArrayed(
                        port.arrayed,
                        pack.arrayable,
                        arrayed_toggle,
                      )}
                      // dim_labels: prefer the runtime value from the
                      // backend (which read the producing port's real
                      // manifest) over the catalog's static value. A
                      // generic port declares placeholder labels
                      // (``["", ""]``) so the paginator can't infer
                      // depth from them alone; the backend fills in
                      // the resolved labels for the ACTUAL producing
                      // port when it can (regroup echoes the input's
                      // dims). Fall back to the effective-static value
                      // when the backend has nothing to add.
                      dimLabels={
                        (target.dim_labels && target.dim_labels.length > 0
                          ? target.dim_labels
                          : effectivePortDimLabels(
                              port.arrayed,
                              port.dim_labels,
                              pack.arrayable,
                              arrayed_toggle,
                            ))
                      }
                      refreshToken={previewRefreshToken}
                    />
                  </>
                );
              }
              return (
                <>
                  {header}
                  <BasicInfoPreview
                    handleId={target.handle_id}
                    storage={target.storage}
                    absolutePath={target.absolute_path}
                  />
                </>
              );
            }
            // Partial preview: no aggregate target yet, but the arrayed
            // fanout has completed at least one shard for this port.
            // Route to Preview() with ``partial={…}`` so ArrayedPaginator
            // pages through the done shards while the parent job is
            // still running. Total is the parent's expected_shards
            // (via ``runtime.progress.total``) so the pager shows
            // ``i / expected`` instead of ``i / done_so_far``.
            const shardsForPort = previewShards?.[name] ?? [];
            const partialAvailable =
              expandKind === "viewer" && !target && shardsForPort.length > 0;
            if (partialAvailable) {
              const expectedTotal = runtime?.progress?.total ?? undefined;
              return (
                <>
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 6,
                      marginBottom: 6,
                      fontSize: 10,
                      color: "var(--inverse-muted)",
                    }}
                  >
                    <span style={{ flex: 1 }}>
                      {pack.name} · {name}
                    </span>
                    <span
                      title="fanout in progress — showing completed shards only"
                      style={{
                        padding: "1px 6px",
                        borderRadius: "var(--radius-pill)",
                        border: "1px solid var(--border-strong)",
                        fontSize: 9,
                        fontFamily: "var(--font-mono)",
                        color: "var(--text-muted)",
                      }}
                    >
                      partial
                    </span>
                  </div>
                  <Preview
                    spec={port.preview ?? undefined}
                    baseUrl=""
                    storage="dir"
                    tags={port.tags}
                    arrayed={effectivePortArrayed(
                      port.arrayed,
                      pack.arrayable,
                      arrayed_toggle,
                    )}
                    dimLabels={effectivePortDimLabels(
                      port.arrayed,
                      port.dim_labels,
                      pack.arrayable,
                      arrayed_toggle,
                    )}
                    partial={{
                      elements: shardsForPort.map((s) => ({
                        element_id: s.element_id,
                        proxy_url: s.proxy_url,
                        handle_id: s.handle_id,
                      })),
                      expectedTotal,
                    }}
                  />
                </>
              );
            }
            // Placeholder for viewer-kind with no/deleted handle, and
            // info-kind with no handle yet (never ran).
            //
            // Don't make a terminal verdict while its evidence is unknown.
            // ``hydrating`` covers the period before history selection has
            // returned; each output then owns its lookup state so one slow
            // folder does not hold unrelated drawers in loading. A settled
            // lookup failure is deliberately neither "cleaned" nor
            // "never-ran".
            const notReady = (hydrating && !runtime) || Boolean(previewLoadingPorts?.[name]);
            const placeholderKind = previewFactsFailedPorts?.[name]
              ? "unavailable"
              : notReady
                ? "loading"
              : previewLoadErrors?.[name]
                ? "unavailable"
              : target?.deleted
                ? "cleaned"
                : "never-ran";
            if (inspector) {
              return <p className="hl-preview-empty">{notReady
                ? "Loading preview…"
                : target?.deleted
                  ? "This artifact has been cleaned up."
                  : "Run this node to preview its output."}</p>;
            }
            // canvas-state already selected this exact handle.  Do not hide
            // the known port contract behind the lower-priority metadata
            // lookup; durable facts fill its dimension labels independently.
            if (placeholderKind === "loading" && outputHandleIds?.[name]) {
              const shape = parseRuntimeObservation(previewFacts?.[name]?.runtime_shape);
              const axes = shape ? observedValue(shape.axes) : undefined;
              const dimensions = axes?.map(axis => {
                const size = observedValue(axis.size);
                return size === undefined ? null : `${axis.label ?? "item"}:${size}`;
              }).filter((value): value is string => value !== null).map(value => `[${value}]`).join("") || null;
              return (
                <PreviewLoadingSkeleton
                  tags={port.tags}
                  arrayed={effectivePortArrayed(port.arrayed, pack.arrayable, arrayed_toggle)}
                  dimensions={dimensions}
                />
              );
            }
            return (
              <PreviewPlaceholder
                kind={placeholderKind}
                packName={pack.name}
                portName={name}
                running={runInFlight}
                readOnly={readOnly}
                onRun={
                  readOnly ? undefined : onRun
                }
              />
            );
          })()}
        </div>
      )}
      {defaultNodeViewer && (
        <div
          className="nodrag"
          style={{
            padding: 8,
            borderTop: "1px solid var(--border)",
            background: "var(--inverse-surface)",
            borderRadius: expanded ? 0 : "0 0 var(--radius-md) var(--radius-md)",
          }}
        >
          <div style={{ fontSize: 10, color: "var(--inverse-muted)", marginBottom: 6 }}>
            {pack.name} · {defaultNodeViewer.title ?? defaultNodeViewer.id}
          </div>
          <NodeViewerFrame
            viewer={defaultNodeViewer}
            packName={pack.name}
            packVersion={pack.version}
            assignedNodeId={assigned_node_id}
            frames={defaultNodeViewerFrames}
            refreshToken={previewRefreshToken}
            historyTarget={
              defaultNodeViewer.history_output
                ? runningHistoryTarget ?? previews?.[defaultNodeViewer.history_output]
                : null
            }
            jobId={runtime?.job_id}
            running={runInFlight}
            progress={runtime?.progress ?? null}
            workflowView={viewer_view}
            workflowViewLoaded={viewer_view_loaded}
            workflowViewManaged={viewer_view_managed}
            onWorkflowDefaultChange={setViewerDefault}
          />
        </div>
      )}
  </>;
}
