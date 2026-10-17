// Shared preview policy and composition. Low-level viewers remain unchanged.
import { useEffect, useMemo, useState } from "react";
import type { CatalogPack, OutputPortSpec } from "../wire";
import type { AlgorithmNodeData, PreviewTarget } from "./AlgorithmNode";
import { effectivePortArrayed, effectivePortDimLabels } from "../tags";
import { useCanvasContext } from "./CanvasContext";
import { CopyRefButton } from "./CopyRefButton";
import { OpenInCocoderButton } from "./OpenInCocoderButton";
import { BasicInfoPreview, Preview } from "./previews";
import { PreviewPlaceholder } from "./PreviewPlaceholder";
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
//     a bespoke viewer (stg-train, regroup-by-frame, colmap-assemble, …)
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

// Tags for which the frontend intercepts Preview() dispatch and picks
// its own viewer regardless of what the backend's tag_viewers.py
// registered (or didn't). Kept in one place so a new frontend-driven
// tag family only has to touch two spots: this set + the intercept
// clause in previews.tsx's Preview() function. Rolling upgrades where
// the running gateway hasn't ingested a new tag_viewers.py entry
// yet — like ``colmap-cams`` at the time of writing — still get the
// caret because this promotion looks at the port's ``tags`` directly.
const FRONTEND_VIEWER_TAGS = new Set<string>([
  "image",
  "colmap-cams",
  // ``point-cloud`` is the 2026-09-22 type-system refactor's canonical
  // tag for triangulated points; ``colmap-points`` was the retired
  // predecessor (colmap-triangulate@0.1.0). Both route to
  // ColmapPointsPreview in previews.tsx.
  "point-cloud",
  "colmap-points",
  // ``colmap-folder`` is the 2026-09-22 refactor's canonical tag for a
  // self-contained COLMAP folder (sparse/0/ + optional images/), emitted
  // by merge-colmap@0.2.0/0.3.0 (which also carries ``colmap`` as a
  // legacy compat alias so stg-train@0.2.0 accepts the edge without a
  // manifest bump). ``colmap-frame`` is the retired
  // colmap-triangulate@0.3.0 tag. All three route to ColmapFramePreview.
  "colmap-folder",
  "colmap",
  "colmap-frame",
  // Rig chain — iframe/ColmapUtil previews. rig_extrinsics is the
  // post-BA rig model; rig_points4d is the per-group triangulation.
  "rig_extrinsics",
  "rig_points4d",
  // Exposure-timeline diagnostic figure (moved from rig-frame-extraction to
  // rig-temporal-grouping@0.1.1, where the buckets are actual data).
  "rig_timeline",
  // rig-capture-source's per-alias camera dirs — 7-tile video grid.
  "rig_capture",
  // Per-alias JPEG tree from rig-frame-extraction / rig-temporal-grouping.
  // Rendered as arrayed<arrayed<image>> via NestedFrameSequencePreview.
  "rig_frames",
  // Per-group directory tree from rig-temporal-grouping@0.1.2 — element =
  // one time bucket, children = the participating aliases' JPEGs. Same
  // NestedFrameSequencePreview shape (element/image tree).
  "rig_frames_grouped",
]);

function hasFrontendViewerTag(tags: string[] | undefined): boolean {
  if (!tags) return false;
  for (const t of tags) {
    if (FRONTEND_VIEWER_TAGS.has(t)) return true;
  }
  return false;
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
    // A port becomes a viewer when EITHER the pack pre-declared one, OR
    // the pack's manifest tags map to a frontend intercept, OR the
    // runtime handle resolved a preview / a frontend-viewer tag. The
    // last clause is what makes generic ports (``regroup.out``:
    // ``tags: [any]`` + ``preview: null`` at catalog time) still open
    // as a viewer once their runtime tag lands as ``["image"]`` — the
    // backend's ``tag_viewers`` resolver has already turned that into a
    // real preview spec on ``target.preview``.
    const staticViewer = spec.preview || hasFrontendViewerTag(spec.tags);
    const runtimeViewer = Boolean(
      target && (target.preview || hasFrontendViewerTag(target.tags)),
    );
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
    const path = `/pack-assets/${encodeURIComponent(assigned_node_id)}/${encodeURIComponent(pack.name)}/${encodeURIComponent(pack.version)}/manifest.yaml`;
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
  const { pack, previews, previewShards, runtime, arrayed_toggle, assigned_node_id, readOnly } = data;
  const { computeNodesById, hydrating } = useCanvasContext();
  const { currentPreview, defaultNodeViewer, defaultNodeViewerFrames } = selection;
  const expanded = currentPreview?.name ?? null;
  const runState = runtime?.state;
  const runInFlight = runState === "pending" || runState === "assigned" || runState === "running";
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
          // Wheel is NOT blocked here: earlier builds stopped the
          // wheel event at the drawer wrapper so the canvas couldn't
          // zoom over an open preview, but that turned the drawer
          // into a wheel black hole. The user wants the natural
          // behavior back — wheel over the drawer zooms the canvas
          // like it does anywhere else, unless a specific descendant
          // opts out via xyflow's ``nowheel`` (none in this file yet).
          className="nodrag hl-node-preview"
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
                      // Prefer the runtime-resolved preview from the
                      // handle response over the catalog's static
                      // declaration. A ``regroup.out`` port declares
                      // ``preview: null`` in the catalog (its element
                      // type is only known at wire time), but the
                      // backend's ``tag_viewers`` resolver turns the
                      // resolved runtime tag into a real spec on
                      // ``target.preview``. When neither is set the
                      // tag intercepts in Preview() still handle
                      // frontend-driven viewers by tag alone.
                      spec={target.preview ?? port.preview ?? undefined}
                      baseUrl={target.proxy_url}
                      storage={target.storage}
                      handleId={target.handle_id}
                      absolutePath={target.absolute_path}
                      producingNode={
                        computeNodesById?.[target.node_id] ?? null
                      }
                      // Tag-driven viewer routing: prefer the runtime
                      // handle's tags (resolved via ``tags_from`` at
                      // handle_register — see gateway/app.py) over the
                      // catalog's static declaration. A ``regroup.out``
                      // handle wired from an ``image`` source arrives
                      // here as ``["image"]``, matching the image
                      // preview; the raw ``port.tags`` would be
                      // ``["any"]`` and skip every intercept in
                      // previews.tsx. Fall back to ``port.tags`` for
                      // legacy handles registered before the resolver
                      // shipped (tags list still populated but never
                      // rewritten).
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
            // "Not ready" gating: don't flash a terminal verdict until we
            // have data that supports one. Two windows to cover:
            //
            //   * ``hydrating`` (from CanvasContext) — the App's initial
            //     cold-load chain (listWorkflowRuns → getSnapshot →
            //     getHandle batch) is still in flight. Persisted
            //     ``preview_open`` slots would otherwise flash "尚未运行"
            //     before jobs arrive, then "产物已被清理" between jobs
            //     landing (runState="done") and the handle batch
            //     resolving (target=null → treated as tombstoned).
            //   * ``runState === "done" && !target`` — post-hydration
            //     WS "done" frame arrived but the async getHandle
            //     hasn't resolved yet. Same "cleaned" flash on live
            //     runs; same fix.
            //
            // Once neither window applies, the terminal branch below
            // fires: ``target?.deleted`` → cleaned (real tombstone),
            // else → never-ran (no done job, no handle).
            const notReady = hydrating || (runState === "done" && !target);
            const placeholderKind = notReady
              ? "loading"
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
            historyTarget={
              defaultNodeViewer.history_output
                ? previews?.[defaultNodeViewer.history_output] ?? null
                : null
            }
            running={runInFlight}
            progress={runtime?.progress ?? null}
          />
        </div>
      )}
  </>;
}
