// Blueprint canvas — the top-level shell.
//
// Layout:
//   - top:    WorkflowToolbar (name, save/run, run summary chip)
//   - left:   PackPalette
//   - middle: xyflow canvas (algorithm-node instances)
//   - right:  ComputeNodesPanel
//   - bottom: NodeInspector + RecentJobsPanel (split half/half)

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  Controls,
  MiniMap,
  Panel,
  ReactFlow,
  ReactFlowProvider,
  addEdge,
  useEdgesState,
  useNodesState,
  useReactFlow,
  useUpdateNodeInternals,
  type Connection,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeChange,
  type OnConnect,
  type ReactFlowInstance,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type {
  CatalogPack,
  ComputeNode,
  Envelope,
  GraphNode as GraphNodeModel,
  JobSummary,
  JobUpdatePayload,
  NodeMetrics,
  NodeOfflinePayload,
  NodeOnlinePayload,
  SnapshotDetail,
  SnapshotJob,
  WorkflowGraph,
} from "./wire";
import {
  ApiError,
  dispatchNode,
  getComputeNodes,
  getHandle,
  getMetricsHistory,
  getPackCatalog,
  getRecentJobs,
  getSnapshot,
  getWorkflow,
  listWorkflowRuns,
  patchWorkflowGraphNodeCosmetic,
  rerunFromNode,
  runWorkflow,
  saveWorkflow,
} from "./api";
import { portsCompatible, effectivePortArrayed } from "./tags";
import { diffGraphs } from "./canvas/diffGraphs";
import type { DiffItem } from "./canvas/diffGraphs";
import {
  computeStaleness,
  earliestDirtyId,
  staleCount,
  stalenessEqual,
  type NodeStaleness,
} from "./canvas/staleness";
import {
  AlgorithmNode,
  PREVIEW_TOGGLE_EVENT,
  RUN_NODE_EVENT,
  type AlgorithmNodeData,
  type NodeRuntime,
  type PreviewShard,
  type PreviewTarget,
  type PreviewToggleDetail,
  type RunNodeDetail,
} from "./canvas/AlgorithmNode";
import { Artifacts } from "./Artifacts";
import { Gallery } from "./Gallery";
import { netBus, useReconnectTick } from "./net";
import { CanvasContext } from "./canvas/CanvasContext";
import { aggregateJobsToRuntime } from "./canvas/nodeRuntime";
import { MobileShell, useIsMobilePortrait } from "./canvas/MobileShell";
import { PackPalette } from "./canvas/PackPalette";
import { ComputeNodesPanel } from "./canvas/ComputeNodesPanel";
import { MinimapToggleButton } from "./canvas/MinimapToggleButton";
import { NodeInspector } from "./canvas/NodeInspector";
import { NodeMetricsPanel, METRICS_HISTORY_LIMIT } from "./canvas/NodeMetricsPanel";
import { EdgeInspector } from "./canvas/EdgeInspector";
import { TypedEdge, type TypedEdgeData } from "./canvas/TypedEdge";
import {
  effectiveOutputType,
  formatTypeLabel,
  formatTypeLabelLong,
} from "./canvas/edgeLabels";
import { RunsPanel } from "./canvas/RunsPanel";
import { useDraftAutosave } from "./canvas/useDraftAutosave";
import { SnapshotBanner } from "./canvas/SnapshotBanner";
import { SnapshotCanvas } from "./canvas/SnapshotCanvas";
import { SnapshotNodeInspector } from "./canvas/SnapshotNodeInspector";
import { Splitter } from "./canvas/Splitter";
import { useResizableSlot } from "./canvas/useResizableLayout";
import { WorkflowToolbar } from "./canvas/WorkflowToolbar";
import {
  RecentJobsPanel,
  summarise,
  type RecentJobRow,
} from "./canvas/RecentJobsPanel";

const NODE_TYPES = { algorithm: AlgorithmNode };

// Cheap value-level equality for the aggregated NodeRuntime so the
// setNodes runtime-sync effect can keep node identity stable across
// WS updates that don't actually move a node's state — reference
// equality alone would fail because runtimeByGraphNode is a fresh
// useMemo output on every job_update tick (see canvas/nodeRuntime.ts
// for the aggregation).
function runtimeEqual(a: NodeRuntime | undefined, b: NodeRuntime | undefined): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  if (a.state !== b.state) return false;
  if (a.job_id !== b.job_id) return false;
  if ((a.fail_reason ?? null) !== (b.fail_reason ?? null)) return false;
  const ap = a.progress ?? null;
  const bp = b.progress ?? null;
  if (ap === bp) return true;
  if (!ap || !bp) return false;
  return ap.current === bp.current && ap.total === bp.total;
}

// A workflow-scoped id counter, distinct from xyflow's internal instance ids.
let ID_COUNTER = 1;
function mintId(prefix: string): string {
  return `${prefix}${Date.now().toString(36)}${(ID_COUNTER++).toString(36)}`;
}

// Tiny path-router: three top-level screens.
//   ``/``               — landing page (Gallery)
//   ``/artifacts``      — cleanup / inventory page
//   ``/w/<id>``         — canvas view for one workflow
//
// Path routing (not hash) because:
//   * ``#w=<id>`` is a URL *fragment*, which the server never sees.
//     Users pasting a workflow URL into a doc or bookmark had a
//     50/50 chance of losing the fragment depending on how the paste
//     tool sanitises it.
//   * Fragments also confused users into thinking the workflow id
//     was cross-tab state — every tab shares localStorage but the
//     hash is per-tab; the misperception was that opening a second
//     tab would move the first one. It doesn't (fragments are
//     per-tab), but path URLs eliminate the ambiguity by looking
//     like real, independent pages.
//
// The server-side SPA fallback (see ``spa_staticfiles.py`` on the
// gateway) hands any non-asset, non-API path back to ``index.html`` so
// a page refresh or a fresh tab on ``/w/<id>`` boots straight into the
// canvas.
//
// No router library — a popstate listener + a switch is ~40 lines and
// makes the SPA fallback contract explicit. react-router would double
// the bundle size for three routes.
type Route =
  | { kind: "gallery" }
  | { kind: "artifacts" }
  | { kind: "canvas"; workflowId: string };

function parseRoute(): Route {
  const path = window.location.pathname;
  if (path === "/artifacts" || path.startsWith("/artifacts/")) {
    return { kind: "artifacts" };
  }
  // ``/w/<id>`` — id is anything up to the next ``/`` or the end.
  const m = /^\/w\/([^/]+)/.exec(path);
  if (m) return { kind: "canvas", workflowId: decodeURIComponent(m[1]) };
  return { kind: "gallery" };
}

/** One-shot migration of legacy hash URLs to their path equivalents.
 *
 *  Old bookmarks and copy-paste artefacts still carry ``/#w=<id>`` or
 *  ``/#artifacts``. Rewrite them via ``history.replaceState`` (no
 *  extra history entry) so refresh / share still lands on the canvas.
 *  Run once at module load, before the first ``parseRoute`` call.
 */
function migrateLegacyHash(): void {
  const hash = window.location.hash;
  if (!hash) return;
  const target = (() => {
    if (hash.startsWith("#artifacts")) return "/artifacts";
    const m = /^#w=([^&]+)/.exec(hash);
    if (m) return `/w/${encodeURIComponent(decodeURIComponent(m[1]))}`;
    return null;
  })();
  if (target) {
    window.history.replaceState(null, "", target);
  }
}
migrateLegacyHash();

/** ``pushState`` doesn't fire ``popstate`` — the browser only does
 *  that for back/forward. Callers do it explicitly so the App-level
 *  router refetches ``parseRoute()`` and re-renders.
 */
function pushPath(path: string): void {
  window.history.pushState(null, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

function navigateToGallery(): void {
  pushPath("/");
}

function navigateToCanvas(workflowId: string): void {
  pushPath(`/w/${encodeURIComponent(workflowId)}`);
}

function navigateToArtifacts(): void {
  pushPath("/artifacts");
}

export default function App() {
  const [route, setRoute] = useState<Route>(() => parseRoute());
  useEffect(() => {
    const onNav = () => setRoute(parseRoute());
    window.addEventListener("popstate", onNav);
    return () => {
      window.removeEventListener("popstate", onNav);
    };
  }, []);

  if (route.kind === "gallery") {
    return (
      <Gallery onOpen={navigateToCanvas} onOpenArtifacts={navigateToArtifacts} />
    );
  }
  if (route.kind === "artifacts") {
    return <Artifacts onBackToGallery={navigateToGallery} />;
  }
  return (
    <ReactFlowProvider>
      <AppInner
        initialWorkflowId={route.workflowId}
        onExitToGallery={navigateToGallery}
      />
    </ReactFlowProvider>
  );
}

interface AppInnerProps {
  // The workflow the router asked us to open. On mount we fetch it and
  // populate the canvas; if the id is unknown we surface an inline
  // error and fall through to a blank canvas rather than white-screen.
  initialWorkflowId: string;
  // Router-level navigate to the gallery. Called from the top-left
  // "← workflows" link and after `New` clears state.
  onExitToGallery: () => void;
}

function AppInner({ initialWorkflowId, onExitToGallery }: AppInnerProps) {
  // Dock measurements are intentionally independent: changing a sidebar does
  // not change either the inspector height or the split inside another dock.
  const leftDock = useResizableSlot("hl-layout-left", 300, 180, 420);
  const rightDock = useResizableSlot("hl-layout-right", 360, 240, 480);
  const bottomDock = useResizableSlot("hl-layout-bottom", 400, 160, Number.POSITIVE_INFINITY);
  const rightSplit = useResizableSlot("hl-layout-right-split", 180, 120, 560);
  const bottomSplit = useResizableSlot("hl-layout-bottom-split", 340, 240, 720);
  const [minimapOpen, setMinimapOpen] = useState(false);

  // Bumped by the WS lifecycle on true reconnects (previously connected →
  // dropped → back). Included in every mount-hydration effect's deps below
  // so a brief gateway restart no longer leaves cards latched in an error
  // state whose ``[handleId]`` / ``[latestSnapshotId]`` deps never change.
  // See ``net.ts`` for the emit semantics; the WS onopen wiring is below.
  const reconnectTick = useReconnectTick();

  // --- catalog + compute nodes -------------------------------------------
  const [catalog, setCatalog] = useState<CatalogPack[]>([]);
  const [computeNodes, setComputeNodes] = useState<ComputeNode[]>([]);
  const [connected, setConnected] = useState(false);
  const [running, setRunning] = useState(false);

  // Per-node rolling metric history for the "server pulse" panel that
  // fills the inspector's empty state. Seeded from the gateway's
  // in-memory buffer on mount and topped up in real time from WS
  // ``node_metrics`` frames. Bounded per node so a long session doesn't
  // grow this map unboundedly — cap matches the gateway ring.
  const [metricsByNode, setMetricsByNode] = useState<Record<string, NodeMetrics[]>>(
    {},
  );

  // Job runtime state — split by scope so a node's status dot only ever
  // reflects the snapshot the canvas is currently showing (draft view =
  // latest snapshot; snapshot view = viewingSnapshot). A flat
  // graph_node_id → NodeRuntime map couldn't represent that: it collapsed
  // multiple runs of the same slot into one identity, and the
  // last-write-wins order was the API's row order — so an older failed
  // job would silently overwrite the current snapshot's done job (see
  // canvas/nodeRuntime.ts for the failure mode + aggregation rules).
  //
  //   - latestSnapshotJobs: the SnapshotJob rows for the workflow's most
  //     recent snapshot. Seeded on workflow open, kept live via WS
  //     job_update patches into this array.
  //   - runtimeByGraphNode: derived (useMemo) — group by graph_node_id
  //     and aggregate for arrayed<T> fan-out (any-failed → failed;
  //     any-in-flight → running; all-done → done). Used only for driving
  //     the badge/progress display on canvas nodes.
  //   - jobsById: job_id → RecentJobRow — a flat feed for the
  //     RecentJobsPanel + workflow-run summary chip. Spans all
  //     snapshots on purpose; it's the "recent activity" surface.
  const [latestSnapshotJobs, setLatestSnapshotJobs] = useState<SnapshotJob[]>([]);
  const [jobsById, setJobsById] = useState<Record<string, RecentJobRow>>({});

  // Preview state, keyed by graph node id:
  //   * previewsByGraphNode: resolved proxy URLs for each output port that
  //     produced a handle AND has a preview declaration.
  //   * previewShardsByGraphNode: partial-preview data for arrayed nodes
  //     while fanout is in progress. Each entry = one done shard's output
  //     handle for a port; the aggregate parent handle lands in
  //     previewsByGraphNode above only after every shard finishes, so
  //     without this map arrayed drawers show nothing (or, worse, the
  //     last-shard's element dir mistaken for the aggregate — subdirs
  //     ``images``/``sparse`` treated as elements). The drawer picks
  //     partial mode when previewsByGraphNode has no target yet but a
  //     shard array is non-empty.
  //   * previewOpenByGraphNode: which drawer is currently expanded per node
  //     (null / missing = collapsed).
  const [previewsByGraphNode, setPreviewsByGraphNode] = useState<
    Record<string, Record<string, PreviewTarget>>
  >({});
  const [previewShardsByGraphNode, setPreviewShardsByGraphNode] = useState<
    Record<string, Record<string, PreviewShard[]>>
  >({});
  // Held on a ref so the PREVIEW_TOGGLE_EVENT listener can read the
  // *current* workflow id when persisting a user toggle, without
  // having to re-register the listener each time workflowId changes.
  // Set below in the workflowId useEffect.
  const workflowIdRef = useRef<string | null>(null);
  // Mirrors ``latestSnapshotId`` so the WS ``job_update`` handler —
  // registered once at mount — can gate upserts into
  // ``latestSnapshotJobs`` by snapshot without re-subscribing on every
  // dispatch. See the handler in the WS effect below for the fan-out
  // shard timing this closes off.
  const latestSnapshotIdRef = useRef<string | null>(null);
  // Set by ``onLoad`` right before it seeds jobs directly from its own
  // ``getSnapshot`` result. Read by the ``latestSnapshotId`` sync
  // effect so it can skip re-fetching the same snapshot on cold-start.
  // Cleared after one use. Any subsequent latestSnapshotId change (a
  // run-then-dispatch, a WS-triggered snapshot bump) falls through to
  // the effect's normal fetch.
  const seededSnapshotIdRef = useRef<string | null>(null);
  const [previewOpenByGraphNode, setPreviewOpenByGraphNode] = useState<
    Record<string, string | null>
  >({});
  // True from the moment ``onLoad`` starts the runs → snapshot → handle
  // fan-out until the full batch commits. Fed to AlgorithmNode via
  // CanvasContext so open-drawer nodes render a neutral "loading"
  // placeholder during that window instead of the flicker sequence
  // "尚未运行 → 产物已被清理 → 蓝色 loading" the persisted
  // ``preview_open`` slot produced on every cold load.
  const [hydrating, setHydrating] = useState<boolean>(false);

  const refreshCatalog = useCallback(async () => {
    try {
      const [cat, nodes] = await Promise.all([getPackCatalog(), getComputeNodes()]);
      setCatalog(cat);
      setComputeNodes(nodes);
    } catch (e) {
      console.warn("catalog refresh failed", e);
    }
  }, []);

  useEffect(() => {
    void refreshCatalog();
    // reconnectTick bump = network came back after a drop; re-hydrate.
  }, [refreshCatalog, reconnectTick]);

  // On first mount, seed the jobs table from REST so a page refresh doesn't
  // clear the recent-runs view.
  useEffect(() => {
    void getRecentJobs()
      .then((rows) => {
        const summaries = rows as unknown as JobSummary[];
        setJobsById((prev) => {
          const next = { ...prev };
          for (const j of summaries) {
            next[j.job_id] = jobToRow(j);
          }
          return next;
        });
        // Deliberately does NOT seed per-graph-node runtime here. The
        // /api/jobs feed spans every snapshot for the workflow, and
        // last-write-wins on graph_node_id lets an older failed job
        // overwrite the current snapshot's done attribution — that was
        // the "canvas red but Run History current is green" bug.
        // Node runtime is derived elsewhere from the visible snapshot's
        // jobs only; the RecentJobsPanel is what wants the flat feed.
      })
      .catch(() => {
        /* ignore — first paint keeps working with empty state */
      });
  }, [reconnectTick]);

  // Seed the pulse panel with whatever history the gateway already
  // buffered so the sparklines aren't empty on first paint. The WS
  // subscription below tops this up in real time.
  useEffect(() => {
    void getMetricsHistory()
      .then((resp) => {
        setMetricsByNode(resp.nodes ?? {});
      })
      .catch(() => {
        /* ignore — WS will populate on the next sample */
      });
  }, [reconnectTick]);

  // --- WS subscription ---------------------------------------------------
  useEffect(() => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const url = `${proto}://${location.host}/ws/frontend`;

    let closed = false;
    let backoff = 1000;

    const connect = () => {
      const ws = new WebSocket(url);
      ws.onopen = () => {
        setConnected(true);
        backoff = 1000;
        // netBus.markConnected only emits ``reconnect`` on a true
        // reconnect (previously connected → dropped → back), so first
        // page-load doesn't cause a redundant re-hydration burst.
        netBus.markConnected();
      };
      ws.onclose = () => {
        setConnected(false);
        netBus.markDisconnected();
        if (!closed) window.setTimeout(connect, backoff);
        backoff = Math.min(backoff * 2, 30000);
      };
      ws.onerror = () => ws.close();
      ws.onmessage = (evt) => {
        try {
          const env = JSON.parse(evt.data) as Envelope;
          handle(env);
        } catch (e) {
          console.warn("bad wire frame", e);
        }
      };
    };

    const handle = (env: Envelope) => {
      if (env.kind === "node_online" || env.kind === "node_offline") {
        void refreshCatalog();
        if (env.kind === "node_online") {
          const _p = env.payload as NodeOnlinePayload;
          void _p;
        }
        if (env.kind === "node_offline") {
          const _p = env.payload as NodeOfflinePayload;
          void _p;
        }
      } else if (env.kind === "node_metrics") {
        // Live per-node CPU / mem / GPU sample. Append into the bounded
        // ring for this node; the pulse panel derives sparklines
        // straight off this state. Skip frames without a node_id (the
        // gateway always stamps one on rebroadcast, but be defensive).
        const p = env.payload as NodeMetrics;
        if (!p.node_id) return;
        const nid = p.node_id;
        setMetricsByNode((prev) => {
          const cur = prev[nid] ?? [];
          const next = cur.concat(p);
          const trimmed =
            next.length > METRICS_HISTORY_LIMIT
              ? next.slice(next.length - METRICS_HISTORY_LIMIT)
              : next;
          return { ...prev, [nid]: trimmed };
        });
      } else if (env.kind === "job_update") {
        const p = env.payload as JobUpdatePayload;

        // Update the jobs table (drives the panel + summary). The wire
        // payload wins for the fields it names; anything else is preserved
        // from the previous snapshot so we don't lose eg the initial
        // created_ts once REST updates roll in.
        setJobsById((prev) => {
          const existing = prev[p.job_id];
          const now = Date.now() / 1000;
          const next: RecentJobRow = {
            ...(existing ?? {
              job_id: p.job_id,
              algorithm_name: p.algorithm_name,
              algorithm_version: p.algorithm_version,
              graph_node_id: p.graph_node_id,
              created_ts: now,
              state: p.state,
              progress: null,
              updated_ts: now,
              fail_reason: null,
            }),
            state: p.state,
            progress: p.progress ?? existing?.progress ?? null,
            fail_reason: p.fail?.reason ?? existing?.fail_reason ?? null,
            updated_ts: now,
            // Preserve started_ts once set; WS payload carries it only on
            // the RUNNING transition frame (null/absent on later frames).
            started_ts: existing?.started_ts ?? p.started_ts ?? null,
            // Fan-out linkage — sticky like started_ts; only shards ever
            // carry these and they don't change over the job's lifetime.
            parent_job_id: existing?.parent_job_id ?? p.parent_job_id ?? null,
            shard_element_id:
              existing?.shard_element_id ?? p.shard_element_id ?? null,
            // Planned shard count — sticky, set once on the parent at
            // fan-out start. Preserve across frames so a later WS update
            // (which omits it on shards) doesn't null it out.
            expected_shards:
              existing?.expected_shards ?? p.expected_shards ?? null,
          };
          // Preserve workflow_id (carried on the wire but not on the panel row shape).
          (next as unknown as { workflow_id?: string }).workflow_id =
            (existing as unknown as { workflow_id?: string })?.workflow_id ??
            p.workflow_id;
          return { ...prev, [p.job_id]: next };
        });

        // Upsert into the visible snapshot's jobs array so the derived
        // runtimeByGraphNode picks up the state change.
        //
        // Why upsert (not just "patch if found"): the fan-out dispatch
        // path creates shard job rows serially in a background task
        // AFTER ``dispatch_single_node`` returns (see
        // ``execution.py:_execute_fanout_body`` — ``_dispatch_shard`` is
        // called per element inside the loop, each triggering a fresh
        // WS ``job_update``). The frontend's ``[latestSnapshotId]``
        // effect kicks off ``getSnapshot`` at endpoint-return time; any
        // shard whose row didn't exist yet when that snapshot detail
        // was read has no anchor for a ``findIndex`` — under the old
        // find-then-patch rule those updates dropped silently, and the
        // node's status dot stayed on the previous aggregate until the
        // user reloaded the page (the "节点运行完了不会立刻在画布上
        // 更新" bug). Upsert with a ``workflow_id`` + ``snapshot_id``
        // gate keeps cross-snapshot leaks out — a stale WS frame from
        // another snapshot of the same workflow (or a different
        // workflow entirely) is dropped instead of appended, so the
        // "canvas red but latest run green" collision the
        // snapshot-scoped design fixed doesn't regress.
        if (
          p.workflow_id === workflowIdRef.current &&
          p.snapshot_id &&
          p.snapshot_id === latestSnapshotIdRef.current
        ) {
          setLatestSnapshotJobs((prev) => {
            const idx = prev.findIndex((j) => j.job_id === p.job_id);
            if (idx >= 0) {
              const next = prev.slice();
              next[idx] = {
                ...next[idx],
                state: p.state,
                progress: p.progress ?? next[idx].progress ?? null,
                fail_reason: p.fail?.reason ?? next[idx].fail_reason ?? null,
                output_handles:
                  p.output_handles ?? next[idx].output_handles ?? null,
                // Sticky like ``parent_job_id``. Preserve so a later
                // shard-authored frame (which omits it) doesn't overwrite
                // the parent's planned count.
                expected_shards:
                  next[idx].expected_shards ?? p.expected_shards ?? null,
              };
              return next;
            }
            // Append — a shard (or otherwise new) job whose row wasn't
            // in the initial snapshot fetch. Fields the ``JobUpdate``
            // payload doesn't carry (params, input_handles, timestamps,
            // etc.) default to empty; the aggregator in
            // canvas/nodeRuntime.ts only reads state / progress /
            // fail_reason / graph_node_id / job_id / parent_job_id /
            // expected_shards from each row, so the partial shape is
            // safe for driving the canvas badge.
            //
            // ``parent_job_id`` MUST be propagated here — without it the
            // aggregator's shard filter (``.parent_job_id != null``)
            // misses this appended shard, falls back to counting all
            // rows, and the denominator inflates by +1 (parent). This
            // was the second half of the "n/n+2 growing" bug.
            const now = Date.now() / 1000;
            const appended: SnapshotJob = {
              job_id: p.job_id,
              workflow_id: p.workflow_id,
              node_id: null,
              graph_node_id: p.graph_node_id,
              algorithm_name: p.algorithm_name,
              algorithm_version: p.algorithm_version,
              state: p.state,
              progress: p.progress ?? null,
              fail_reason: p.fail?.reason ?? null,
              fail_exit_code: p.fail?.exit_code ?? null,
              fail_message: p.fail?.message ?? null,
              params: {},
              input_handles: {},
              output_handles: p.output_handles ?? null,
              parent_job_id: p.parent_job_id ?? null,
              shard_element_id: p.shard_element_id ?? null,
              expected_shards: p.expected_shards ?? null,
              created_ts: now,
              updated_ts: now,
            };
            return prev.concat(appended);
          });
        }

        // On the terminal transition to done, the gateway includes
        // output_handles in the frame. Resolve each to a preview target
        // (proxy URL + storage form) via /api/handles/{id}. The result
        // powers the in-canvas expand drawer for viewer-declared ports.
        //
        // Shard vs parent routing: fan-out shard jobs also fire ``done``
        // frames carrying their own graph_node_id + output_handles, but
        // each shard's ``frame`` handle points at ONE element dir
        // (``<parent_ws>/frame/<element_id>/``), not the aggregate. If
        // we let those overwrite ``previewsByGraphNode[gnid][port]``,
        // the ArrayedPaginator opens that element dir and treats its
        // internal subdirs (``images``, ``sparse``) as "elements" —
        // hence the "images 1/2 + HTTP 404 images.txt" bug during
        // triangulate fanouts. Route shards into
        // ``previewShardsByGraphNode`` instead; the drawer picks
        // partial mode from there while ``previewsByGraphNode`` waits
        // for the parent's aggregate.
        if (p.state === "done" && p.graph_node_id && p.output_handles) {
          const gnid = p.graph_node_id;
          const handles = p.output_handles;
          const shardEl = p.shard_element_id ?? null;
          const isShard = p.parent_job_id != null && shardEl != null;
          void Promise.all(
            Object.entries(handles).map(async ([port_name, handle_id]) => {
              try {
                const info = await getHandle(handle_id);
                return [port_name, info] as const;
              } catch (err) {
                console.warn("handle lookup failed", err);
                return null;
              }
            }),
          ).then((resolved) => {
            if (isShard && shardEl) {
              setPreviewShardsByGraphNode((prev) => {
                const gn = { ...(prev[gnid] ?? {}) };
                let changed = false;
                for (const r of resolved) {
                  if (!r) continue;
                  const [port_name, info] = r;
                  const port = gn[port_name] ? [...gn[port_name]] : [];
                  if (port.some((e) => e.element_id === shardEl)) continue;
                  port.push({
                    element_id: shardEl,
                    handle_id: info.handle_id,
                    proxy_url: info.proxy_url,
                    storage: info.storage as "dir" | "file",
                  });
                  port.sort((a, b) =>
                    a.element_id.localeCompare(b.element_id, undefined, {
                      numeric: true,
                      sensitivity: "base",
                    }),
                  );
                  gn[port_name] = port;
                  changed = true;
                }
                if (!changed) return prev;
                return { ...prev, [gnid]: gn };
              });
              return;
            }
            const targets: Record<string, PreviewTarget> = {};
            for (const r of resolved) {
              if (!r) continue;
              const [port_name, info] = r;
              targets[port_name] = {
                port_name,
                handle_id: info.handle_id,
                node_id: info.node_id,
                proxy_url: info.proxy_url,
                storage: info.storage as "dir" | "file",
                absolute_path: info.absolute_path,
                deleted: info.deleted_ts !== null,
                tags: info.tags,
                preview: info.preview ?? null,
                dim_labels: info.dim_labels ?? null,
                dim_sizes: info.dim_sizes ?? null,
              };
            }
            if (Object.keys(targets).length === 0) return;
            setPreviewsByGraphNode((prev) => ({
              ...prev,
              [gnid]: { ...(prev[gnid] ?? {}), ...targets },
            }));
          });
        }
      }
    };

    connect();
    return () => {
      closed = true;
    };
  }, [refreshCatalog]);

  // --- workflow model ---------------------------------------------------
  const [workflowId, setWorkflowId] = useState<string | null>(null);
  const [workflowName, setWorkflowName] = useState<string>("untitled");
  // The past run (if any) the user has opened on the canvas via the
  // Runs panel. When non-null the middle column swaps from the editable
  // draft ReactFlow to a read-only SnapshotCanvas and a "read-only" banner
  // strip sits at the top. Setting this back to null returns to the draft.
  const [viewingSnapshot, setViewingSnapshot] = useState<SnapshotDetail | null>(null);
  const [snapshotError, setSnapshotError] = useState<string | null>(null);
  // Independent selection state for the snapshot canvas so it doesn't
  // collide with the draft's xyflow selection when we toggle in/out.
  const [snapshotSelectedGraphNodeId, setSnapshotSelectedGraphNodeId] = useState<
    string | null
  >(null);
  // Snapshot-view edge selection: lifted to App so the App-owned
  // bottom inspector can switch between NodeInspector and EdgeInspector.
  // (The draft view derives ``selectedEdge`` from xyflow's own
  // ``edge.selected`` flag — same pattern the existing draft node
  // selection uses via ``selectedNode``.)
  const [snapshotSelectedEdgeId, setSnapshotSelectedEdgeId] = useState<
    string | null
  >(null);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node<AlgorithmNodeData>>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge<TypedEdgeData>>([]);
  // Stable refs so onSelectEdge doesn't depend on edges/nodes and
  // doesn't need to be recreated (which would remount all TypedEdge
  // instances via edgeTypes useMemo).
  const edgesRef = useRef(edges);
  edgesRef.current = edges;
  const nodesRef = useRef(nodes);
  nodesRef.current = nodes;
  // Controlled-state callback for chip clicks: selects this edge and
  // deselects all others + all nodes, so EdgeInspector reliably reflects
  // the selection (rf.setEdges bypasses the controlled state and gets
  // overwritten on the next render that re-applies the edges prop).
  const onSelectEdge = useCallback(
    (id: string) => {
      onEdgesChange(
        edgesRef.current.map((e) => ({
          id: e.id,
          type: "select" as const,
          selected: e.id === id,
        })),
      );
      onNodesChange(
        nodesRef.current
          .filter((n) => n.selected)
          .map((n) => ({ id: n.id, type: "select" as const, selected: false })),
      );
    },
    [onEdgesChange, onNodesChange],
  );
  const edgeTypes = useMemo(
    () => ({
      typed: (props: EdgeProps) => (
        <TypedEdge {...props} onSelect={onSelectEdge} />
      ),
    }),
    [onSelectEdge],
  );
  // Imperative "re-measure this node" — used defensively after graph
  // hydration to nudge xyflow into re-parsing handle positions even
  // when the DOM element's dimensions didn't change (e.g. a fresh
  // page load whose initial adopt-then-adopt sequence races the
  // ResizeObserver's first callback and leaves ``handleBounds``
  // stuck at ``undefined``). Cheap: it just schedules a rAF that
  // measures the still-existing DOM. See canvas/CanvasContext.ts
  // for the full failure-mode notes.
  const updateNodeInternals = useUpdateNodeInternals();
  // Frozen graph from the most-recent snapshot. Null when no snapshot exists
  // yet. Used by draftDiff to populate the "草稿有结构改动" sentinel row.
  const [latestSnapshotGraph, setLatestSnapshotGraph] = useState<WorkflowGraph | null>(
    null,
  );
  // ID of the most-recent snapshot for this workflow. Null until hydration.
  // Used to highlight the "当前" chip in RunsPanel while in draft view.
  const [latestSnapshotId, setLatestSnapshotId] = useState<string | null>(null);
  // Incremented after every single-node dispatch so RunsPanel re-fetches its
  // list and shows the new snapshot row without requiring a manual Refresh.
  const [runsPanelRefreshToken, setRunsPanelRefreshToken] = useState(0);

  // Node runtime = aggregate of the visible snapshot's jobs (draft view →
  // latest snapshot; snapshot view → viewingSnapshot). Fan-out aggregation
  // lives in canvas/nodeRuntime.ts. Memoised so the setNodes runtime-sync
  // effect only fires when the underlying jobs actually change.
  const runtimeByGraphNode = useMemo(
    () => aggregateJobsToRuntime(viewingSnapshot?.jobs ?? latestSnapshotJobs),
    [viewingSnapshot, latestSnapshotJobs],
  );

  // Keep latestSnapshotJobs synced with latestSnapshotId. Covers both
  // page-load hydration and the single-node-dispatch case where the
  // handler bumps latestSnapshotId to a freshly-created snapshot.
  //
  // The ref update below runs first so a WS ``job_update`` arriving
  // between the state commit and the fetch's return knows which
  // snapshot the array now represents — without it, an upsert would
  // either drop (stale ref = prior snapshot) or leak (ref never
  // moved). Clearing the array before the fetch keeps stale rows
  // from a previous snapshot out of the merge window: they'd
  // otherwise be preserved as "not in fetch" entries.
  //
  // Merge on fetch (not overwrite) protects against the narrow race
  // where a background-created shard's WS frame arrives before the
  // fetch's DB read caught the same shard row: without the merge,
  // the fetch would replace the WS-appended row with a shard-less
  // snapshot and, absent another WS frame for that shard, the node
  // would show a stale sub-state (e.g. shard-pending seen via WS,
  // never re-fetched because we already have the id).
  useEffect(() => {
    latestSnapshotIdRef.current = latestSnapshotId;
    if (!latestSnapshotId) {
      setLatestSnapshotJobs([]);
      return;
    }
    // Cold-load skip: onLoad's hydration loop already fetched this
    // snapshot and seeded jobs. Firing another getSnapshot here would
    // (a) clear the seeded jobs, then (b) issue a duplicate network
    // request. Consume the ref and short-circuit. The seeded jobs
    // remain in place; WS ``job_update`` frames still apply via the
    // handler in the ws-effect below.
    if (seededSnapshotIdRef.current === latestSnapshotId) {
      seededSnapshotIdRef.current = null;
      return;
    }
    setLatestSnapshotJobs([]);
    let cancelled = false;
    void getSnapshot(latestSnapshotId)
      .then((snap) => {
        if (cancelled) return;
        // Update the frozen graph so draftDiff clears immediately after any
        // run (dispatchNode or runWorkflow) without requiring a page refresh.
        setLatestSnapshotGraph(snap.graph);
        setLatestSnapshotJobs((prev) => {
          const byId = new Map<string, SnapshotJob>();
          for (const j of snap.jobs) byId.set(j.job_id, j);
          for (const j of prev) {
            if (!byId.has(j.job_id)) byId.set(j.job_id, j);
          }
          return Array.from(byId.values());
        });
      })
      .catch(() => {
        /* leave prior jobs in place — WS updates still apply */
      });
    return () => {
      cancelled = true;
    };
  }, [latestSnapshotId, reconnectTick]);

  const catalogByKey = useMemo(() => {
    const m = new Map<string, CatalogPack>();
    for (const p of catalog) m.set(`${p.name}@${p.version}`, p);
    return m;
  }, [catalog]);

  // ``pendingRemeasureRef`` is declared up here (before every useEffect
  // that seeds it) so its scope covers both the catalog-refresh effect
  // below and the ``fromGraph`` hydrator further down. Drained by the
  // effect that calls ``updateNodeInternals`` — see the block comment
  // there for the failure mode this backstops.
  const pendingRemeasureRef = useRef<string[]>([]);

  // Refresh AlgorithmNode.data.pack when the catalog changes so signatures
  // stay accurate after a pack edit.
  //
  // Identity preservation is load-bearing here. ``refreshCatalog`` fires on
  // every WS ``node_online`` / ``node_offline`` (App.tsx:345), which mints
  // fresh CatalogPack objects even when nothing structurally changed. If
  // this effect naively creates a new node identity for every pack lookup,
  // xyflow's ``adoptUserNodes`` re-inits the internal node and — if the
  // browser's ResizeObserver hasn't ticked yet — resets ``handleBounds`` to
  // undefined, silently dropping every edge attached to those handles. See
  // the block comment above ``pendingRemeasureRef`` for the full sequence;
  // the user hit this as "刷新后中间的连线都没了". Guard by comparing
  // ``manifest_hash`` (the manifest's SHA — a stable content id): unchanged
  // hash means the exec / inputs / outputs shape is identical, so there's
  // no reason to churn identity, and any node_ids drift for the "open in
  // source" fallback is acceptably stale. When the hash DOES change (real
  // pack edit), we churn AND seed ``pendingRemeasureRef`` so the effect
  // below re-parses handle positions for the affected nodes.
  useEffect(() => {
    setNodes((current) => {
      const remeasure: string[] = [];
      const next = current.map((n) => {
        const key = `${n.data.pack.name}@${n.data.pack.version}`;
        const fresh = catalogByKey.get(key);
        if (!fresh || fresh.manifest_hash === n.data.pack.manifest_hash) {
          return n;
        }
        remeasure.push(n.id);
        return { ...n, data: { ...n.data, pack: fresh } };
      });
      if (remeasure.length > 0) {
        pendingRemeasureRef.current = [
          ...pendingRemeasureRef.current,
          ...remeasure,
        ];
      }
      return next;
    });
  }, [catalogByKey, setNodes]);

  // Compute-node lookup keyed by node_id. Shared with AlgorithmNode via
  // ``CanvasContext`` — deliberately NOT baked into per-node ``data``
  // because a WS-triggered compute-nodes refresh would then force every
  // node object to be recreated. That churn used to race with the
  // initial ResizeObserver measurement pass: xyflow's ``adoptUserNodes``
  // resets ``handleBounds`` for any userNode without ``measured``, and
  // if the reset landed before the browser had measured the DOM, edges
  // attached to those handles silently disappeared (only nodes whose
  // dimensions later changed — e.g. drawer open/close — recovered on
  // the next resize tick). Context reads stay fresh without touching
  // node identities. See ``canvas/CanvasContext.ts``.
  const computeNodesById = useMemo<Record<string, ComputeNode>>(() => {
    const out: Record<string, ComputeNode> = {};
    for (const cn of computeNodes) out[cn.node_id] = cn;
    return out;
  }, [computeNodes]);

  const canvasContextValue = useMemo(
    () => ({ workflow_id: workflowId, computeNodesById, hydrating }),
    [workflowId, computeNodesById, hydrating],
  );

  // Push job runtime + resolved previews + previewOpen into each node's
  // data. This drives the status badge, expand caret, and inline preview
  // surface. Nodes whose relevant fields haven't changed keep their
  // identity so xyflow doesn't have to re-adopt them (see the
  // ``handleBounds``-reset failure mode above).
  useEffect(() => {
    setNodes((current) =>
      current.map((n) => {
        const d = n.data as AlgorithmNodeData;
        const rt = runtimeByGraphNode[n.id];
        const pv = previewsByGraphNode[n.id];
        const ps = previewShardsByGraphNode[n.id];
        const po = previewOpenByGraphNode[n.id] ?? null;
        // Value-level runtime compare so a WS update that keeps a node's
        // aggregate state unchanged doesn't mint a new node identity —
        // xyflow's adoptUserNodes would otherwise reset handleBounds and
        // drop edges (see canvas/CanvasContext.ts for the failure mode).
        // runtimeByGraphNode is a fresh useMemo output on every WS
        // update so reference equality alone would churn every node.
        if (
          runtimeEqual(rt, d.runtime) &&
          pv === d.previews &&
          ps === d.previewShards &&
          po === (d.previewOpen ?? null)
        ) {
          return n;
        }
        return {
          ...n,
          data: {
            ...n.data,
            runtime: rt,
            previews: pv,
            previewShards: ps,
            previewOpen: po,
          },
        };
      }),
    );
  }, [
    runtimeByGraphNode,
    previewsByGraphNode,
    previewShardsByGraphNode,
    previewOpenByGraphNode,
    setNodes,
  ]);

  // Bridge the AlgorithmNode's expand caret (fires a DOM CustomEvent) back
  // into App-level state. We do it this way because xyflow's nodeTypes
  // registry doesn't take callback props — using an event keeps
  // AlgorithmNode a pure renderer that reads from data.
  useEffect(() => {
    const onToggle = (evt: Event) => {
      const detail = (evt as CustomEvent<PreviewToggleDetail>).detail;
      setPreviewOpenByGraphNode((prev) => ({
        ...prev,
        [detail.graph_node_id]: detail.port_name,
      }));
      // Fire-and-forget cosmetic patch — the endpoint updates the
      // draft's ``graph.preview_open`` and mirrors to the workflow's
      // most recent snapshot. We read ``workflowId`` off a ref so the
      // listener doesn't have to be re-registered every time the
      // workflow changes. Network failure just means the state
      // survives this session but not the next refresh — the drawer
      // still visually toggles because state was updated locally
      // first.
      const wid = workflowIdRef.current;
      if (wid) {
        void patchWorkflowGraphNodeCosmetic(wid, detail.graph_node_id, {
          preview_open: detail.port_name,
        }).catch((err) => {
          console.warn("preview_open patch failed", err);
        });
      }
    };
    window.addEventListener(PREVIEW_TOGGLE_EVENT, onToggle);
    return () => window.removeEventListener(PREVIEW_TOGGLE_EVENT, onToggle);
  }, []);

  // Bridge the preview placeholder's "run this node" button. Uses the
  // V8 Continue-or-Fork dispatch endpoint — no new semantics: if the
  // graph node has no artifact yet in the latest snapshot the server
  // Continues; otherwise it Forks. When no workflow is loaded (fresh
  // palette drop, no save yet) we reject so the placeholder can show
  // an error instead of silently doing nothing.
  useEffect(() => {
    const onRun = (evt: Event) => {
      const detail = (evt as CustomEvent<RunNodeDetail>).detail;
      // Ack the event synchronously so ``dispatchRunNode`` can tell "no
      // listener registered" (would silently hang) from "listener took
      // over but the fetch is still pending". See RunNodeDetail.handled.
      detail.handled = true;
      const { graph_node_id, resolve, reject } = detail;
      const wid = workflowIdRef.current;
      if (!wid) {
        reject("save the workflow first (no workflow_id yet)");
        return;
      }
      // Guard against ``dispatchNode`` throwing synchronously (URL
      // construction, non-fetch network stacks): without this, the
      // promise would never resolve and the caller's button would hang
      // until the dispatchRunNode timeout kicks in.
      try {
        dispatchNode(wid, graph_node_id).then(
          (result) => {
            setLatestSnapshotId(result.snapshot_id);
            setRunsPanelRefreshToken((t) => t + 1);
            resolve();
          },
          (err: Error) => reject(err?.message || "dispatch failed"),
        );
      } catch (err) {
        reject(err instanceof Error ? err.message : String(err));
      }
    };
    window.addEventListener(RUN_NODE_EVENT, onRun);
    return () => window.removeEventListener(RUN_NODE_EVENT, onRun);
  }, []);

  // Mirror workflowId into a ref so the toggle listener (registered
  // once at mount) can read the current value without re-registering.
  useEffect(() => {
    workflowIdRef.current = workflowId;
  }, [workflowId]);

  // Workflow-scoped jobs feed the RecentJobsPanel and summary chip.
  const workflowJobs = useMemo<RecentJobRow[]>(() => {
    const all = Object.values(jobsById);
    const scoped = workflowId
      ? all.filter(
          (j) =>
            (j as unknown as { workflow_id?: string }).workflow_id === workflowId,
        )
      : all;
    return scoped.sort((a, b) => b.created_ts - a.created_ts);
  }, [jobsById, workflowId]);

  const runSummary = useMemo(() => summarise(workflowJobs), [workflowJobs]);

  // --- xyflow onConnect: tag-check before adding the edge --------------
  const onConnect: OnConnect = useCallback(
    (conn: Connection) => {
      if (!conn.source || !conn.target || !conn.sourceHandle || !conn.targetHandle) return;

      const src = nodes.find((n) => n.id === conn.source)?.data;
      const tgt = nodes.find((n) => n.id === conn.target)?.data;
      if (!src || !tgt) return;

      const srcOut = src.pack.outputs[conn.sourceHandle];
      const tgtIn = tgt.pack.inputs[conn.targetHandle];
      const srcTags = srcOut?.tags ?? [];
      const tgtTags = tgtIn?.tags ?? [];
      // Effective arrayed state depends on both the manifest declaration
      // and the per-node ``arrayed_toggle`` (when the pack is arrayable).
      // We read the toggle off the node data if present; unset → false.
      const srcNode = nodes.find((n) => n.id === conn.source);
      const tgtNode = nodes.find((n) => n.id === conn.target);
      const srcArrayed = effectivePortArrayed(
        srcOut?.arrayed,
        src.pack.arrayable,
        (srcNode?.data as { arrayed_toggle?: boolean } | undefined)?.arrayed_toggle,
        srcOut?.scalar,
      );
      const tgtArrayed = effectivePortArrayed(
        tgtIn?.arrayed,
        tgt.pack.arrayable,
        (tgtNode?.data as { arrayed_toggle?: boolean } | undefined)?.arrayed_toggle,
        tgtIn?.scalar,
      );
      if (!portsCompatible(srcTags, srcArrayed, tgtTags, tgtArrayed, tgtIn?.scalar)) {
        // Flash a message via console for MVP; a toast is a follow-up.
        const srcLabel = srcArrayed ? `arrayed<${srcTags.join(",")}>` : srcTags.join(",");
        const tgtLabel = tgtArrayed ? `arrayed<${tgtTags.join(",")}>` : tgtTags.join(",");
        console.warn(`edge rejected: ${srcLabel} → ${tgtLabel} incompatible`);
        return;
      }

      setEdges((es) =>
        addEdge(
          {
            ...conn,
            id: mintId("e"),
            animated: false,
            type: "typed",
            data: {},
          },
          es,
        ),
      );
    },
    [nodes, setEdges],
  );

  // --- drop target: convert a dragged pack into a canvas node ----------
  const rfWrapper = useRef<HTMLDivElement>(null);
  const [rfInstance, setRfInstance] = useState<ReactFlowInstance | null>(null);

  // --- dynamic minZoom: fit the full node chain horizontally ----------
  const [dynamicMinZoom, setDynamicMinZoom] = useState<number>(0.05);
  const minZoomTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const computeMinZoom = useCallback(() => {
    if (!rfWrapper.current || nodes.length === 0) return;
    const vpWidth = rfWrapper.current.getBoundingClientRect().width;
    if (vpWidth <= 0) return;
    let xMin = Infinity, xMax = -Infinity;
    for (const n of nodes) {
      const w = (n.measured as { width?: number } | undefined)?.width ?? 200;
      if (n.position.x < xMin) xMin = n.position.x;
      if (n.position.x + w > xMax) xMax = n.position.x + w;
    }
    const contentWidth = xMax - xMin;
    if (!isFinite(contentWidth) || contentWidth <= 0) return;
    const fitWidth = (vpWidth - 40) / contentWidth;
    setDynamicMinZoom(Math.max(0.05, Math.min(0.5, fitWidth * 0.9)));
  }, [nodes]);

  const scheduleMinZoom = useCallback(() => {
    if (minZoomTimerRef.current) clearTimeout(minZoomTimerRef.current);
    minZoomTimerRef.current = setTimeout(computeMinZoom, 200);
  }, [computeMinZoom]);

  useEffect(() => { scheduleMinZoom(); }, [nodes, scheduleMinZoom]);

  useEffect(() => {
    const el = rfWrapper.current;
    if (!el) return;
    const ro = new ResizeObserver(scheduleMinZoom);
    ro.observe(el);
    return () => ro.disconnect();
  }, [scheduleMinZoom]);

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  }, []);

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      if (!rfInstance || !rfWrapper.current) return;
      const raw = e.dataTransfer.getData("application/hololab-pack");
      if (!raw) return;
      const { name, version } = JSON.parse(raw) as { name: string; version: string };
      const pack = catalogByKey.get(`${name}@${version}`);
      if (!pack) return;

      const position = rfInstance.screenToFlowPosition({
        x: e.clientX,
        y: e.clientY,
      });

      const node: Node<AlgorithmNodeData> = {
        id: mintId("n"),
        type: "algorithm",
        position,
        data: {
          pack,
          assigned_node_id: pack.node_ids[0] ?? null, // default to the first eligible
          ...({ arrayed_toggle: false, parallelism: 1 } as object),
        },
      };
      setNodes((ns) => ns.concat(node));
    },
    [rfInstance, catalogByKey, setNodes],
  );

  // --- selection → NodeInspector ---------------------------------------
  const selectedNode = useMemo(() => nodes.find((n) => n.selected) || null, [nodes]);
  const selectedEdge = useMemo(() => edges.find((e) => e.selected) || null, [edges]);

  // --- edge type labels ------------------------------------------------
  //
  // Re-project the draft graph into ``{GraphNode[], GraphEdge[]}`` shape
  // for ``effectiveOutputType`` to walk (it needs ``tags_from`` back-refs
  // via edges). We do it once per (nodes, edges, catalog) change and
  // patch the label onto each edge's data. Object identity of the edge
  // is preserved when the label didn't change so xyflow doesn't churn.
  const displayEdges = useMemo(() => {
    if (edges.length === 0) return edges;
    const graphNodes = nodes.map((n) => {
      const d = n.data as AlgorithmNodeData & {
        params?: Record<string, unknown>;
        arrayed_toggle?: boolean;
      };
      return {
        id: n.id,
        algorithm_name: n.data.pack.name,
        algorithm_version: n.data.pack.version,
        position: n.position,
        // Real params — effectiveOutputType reads dim_labels_from here
        // (e.g. regroup.out → output_dims) so the chip renders the
        // right labels before any handle summary lands.
        params: d.params ?? {},
        assigned_node_id: n.data.assigned_node_id,
        arrayed_toggle: Boolean(d.arrayed_toggle),
      };
    });
    const graphEdges = edges.map((e) => ({
      id: e.id,
      source: e.source,
      sourceHandle: e.sourceHandle ?? "",
      target: e.target,
      targetHandle: e.targetHandle ?? "",
    }));
    return edges.map((e) => {
      const t = effectiveOutputType(e.source, e.sourceHandle ?? "", {
        nodes: graphNodes,
        edges: graphEdges,
        catalogByKey,
      });
      const label = formatTypeLabel(t);
      const labelLong = formatTypeLabelLong(t);
      const sourceHandleName = e.sourceHandle ?? "";
      const handleId =
        previewsByGraphNode[e.source]?.[sourceHandleName]?.handle_id ?? null;
      const prev = e.data as TypedEdgeData | undefined;
      if (
        prev?.label === label &&
        prev.labelLong === labelLong &&
        prev.handleId === handleId &&
        prev.edgeType === t
      ) {
        return e;
      }
      return {
        ...e,
        data: {
          ...(prev ?? {}),
          label,
          labelLong,
          edgeType: t,
          handleId,
        },
      };
    });
  }, [edges, nodes, catalogByKey, previewsByGraphNode]);

  // Snapshot-mode counterparts. Derived from viewingSnapshot +
  // snapshotSelectedGraphNodeId so the read-only inspector reflects
  // whatever node the user has clicked on the frozen canvas.
  const snapshotSelectedJob = useMemo(() => {
    if (!viewingSnapshot || !snapshotSelectedGraphNodeId) return null;
    return (
      viewingSnapshot.jobs.find(
        (j) => j.graph_node_id === snapshotSelectedGraphNodeId,
      ) ?? null
    );
  }, [viewingSnapshot, snapshotSelectedGraphNodeId]);

  const snapshotSelectedPack = useMemo(() => {
    if (!viewingSnapshot || !snapshotSelectedGraphNodeId) return null;
    const gn = viewingSnapshot.graph.nodes.find(
      (n) => n.id === snapshotSelectedGraphNodeId,
    );
    if (!gn) return null;
    return catalogByKey.get(`${gn.algorithm_name}@${gn.algorithm_version}`) ?? null;
  }, [viewingSnapshot, snapshotSelectedGraphNodeId, catalogByKey]);

  // Compose props for the EdgeInspector. Draft vs snapshot: both funnel
  // into the same component with the same shape; the difference is only
  // *where* the source handle id comes from (draft: previewsByGraphNode
  // lookup; snapshot: SnapshotJob.output_handles). Returns null when
  // nothing is selected or when the selected edge can't be resolved
  // (source node disappeared etc.).
  const edgeInspectorProps = useMemo(() => {
    if (viewingSnapshot) {
      if (!snapshotSelectedEdgeId) return null;
      const ge = viewingSnapshot.graph.edges.find(
        (e) => e.id === snapshotSelectedEdgeId,
      );
      if (!ge) return null;
      const srcNode = viewingSnapshot.graph.nodes.find((n) => n.id === ge.source);
      const tgtNode = viewingSnapshot.graph.nodes.find((n) => n.id === ge.target);
      if (!srcNode || !tgtNode) return null;
      const srcPack =
        catalogByKey.get(
          `${srcNode.algorithm_name}@${srcNode.algorithm_version}`,
        ) ?? null;
      const tgtPack =
        catalogByKey.get(
          `${tgtNode.algorithm_name}@${tgtNode.algorithm_version}`,
        ) ?? null;
      const portSpec = srcPack?.outputs[ge.sourceHandle] ?? null;
      const type = effectiveOutputType(ge.source, ge.sourceHandle, {
        nodes: viewingSnapshot.graph.nodes,
        edges: viewingSnapshot.graph.edges,
        catalogByKey,
      });
      // Source handle from the frozen job for this graph node (if any
      // job attributed to this slot produced named outputs).
      const srcJob = viewingSnapshot.jobs.find(
        (j) => j.graph_node_id === ge.source,
      );
      const handleId = srcJob?.output_handles?.[ge.sourceHandle] ?? null;
      // We don't know the producing compute node without a getHandle
      // round-trip; the inspector fetches HandleInfo internally which
      // carries node_id — but the OpenInCocoderButton needs the full
      // ComputeNode object. Pass null here; the button will render but
      // show the "not configured" guide until the user connects it.
      // (A follow-up can resolve node_id → ComputeNode after the
      // handle loads.)
      const computeNode = null;
      return {
        edgeId: ge.id,
        edgeType: type,
        source: {
          node: srcNode,
          pack: srcPack,
          portName: ge.sourceHandle,
          portSpec,
          handleId,
          computeNode,
        },
        target: {
          node: tgtNode,
          pack: tgtPack,
          portName: ge.targetHandle,
        },
      };
    }
    // Draft view.
    if (!selectedEdge) return null;
    const srcRf = nodes.find((n) => n.id === selectedEdge.source);
    const tgtRf = nodes.find((n) => n.id === selectedEdge.target);
    if (!srcRf || !tgtRf) return null;
    const srcPack = srcRf.data.pack;
    const tgtPack = tgtRf.data.pack;
    const sourceHandle = selectedEdge.sourceHandle ?? "";
    const targetHandle = selectedEdge.targetHandle ?? "";
    const portSpec = srcPack.outputs[sourceHandle] ?? null;
    // Rebuild GraphNode/Edge shape for effectiveOutputType (matches
    // displayEdges above; kept inline because the args differ enough
    // that pulling into a helper wouldn't pay for itself).
    const graphNodes = nodes.map((n) => {
      const d = n.data as AlgorithmNodeData & {
        params?: Record<string, unknown>;
        arrayed_toggle?: boolean;
      };
      return {
        id: n.id,
        algorithm_name: n.data.pack.name,
        algorithm_version: n.data.pack.version,
        position: n.position,
        params: d.params ?? {},
        assigned_node_id: n.data.assigned_node_id,
        arrayed_toggle: Boolean(d.arrayed_toggle),
      };
    });
    const graphEdges = edges.map((e) => ({
      id: e.id,
      source: e.source,
      sourceHandle: e.sourceHandle ?? "",
      target: e.target,
      targetHandle: e.targetHandle ?? "",
    }));
    const type = effectiveOutputType(selectedEdge.source, sourceHandle, {
      nodes: graphNodes,
      edges: graphEdges,
      catalogByKey,
    });
    const target = previewsByGraphNode[selectedEdge.source]?.[sourceHandle];
    const computeNode =
      target && target.node_id ? computeNodesById[target.node_id] ?? null : null;
    // Build a synthetic GraphNode from the react-flow node so the
    // EdgeInspector doesn't have to know the difference.
    const srcNode: GraphNodeModel = {
      id: srcRf.id,
      algorithm_name: srcPack.name,
      algorithm_version: srcPack.version,
      position: srcRf.position,
      params: {},
      assigned_node_id: srcRf.data.assigned_node_id,
      arrayed_toggle: Boolean(
        (srcRf.data as AlgorithmNodeData & { arrayed_toggle?: boolean })
          .arrayed_toggle,
      ),
    };
    const tgtNode: GraphNodeModel = {
      id: tgtRf.id,
      algorithm_name: tgtPack.name,
      algorithm_version: tgtPack.version,
      position: tgtRf.position,
      params: {},
      assigned_node_id: tgtRf.data.assigned_node_id,
      arrayed_toggle: Boolean(
        (tgtRf.data as AlgorithmNodeData & { arrayed_toggle?: boolean })
          .arrayed_toggle,
      ),
    };
    return {
      edgeId: selectedEdge.id,
      edgeType: type,
      source: {
        node: srcNode,
        pack: srcPack,
        portName: sourceHandle,
        portSpec,
        handleId: target?.handle_id ?? null,
        computeNode,
      },
      target: {
        node: tgtNode,
        pack: tgtPack,
        portName: targetHandle,
      },
    };
  }, [
    viewingSnapshot,
    snapshotSelectedEdgeId,
    selectedEdge,
    nodes,
    edges,
    catalogByKey,
    previewsByGraphNode,
    computeNodesById,
  ]);

  const onInspectorChange = useCallback(
    (patch: Partial<GraphNodeModel>) => {
      if (!selectedNode) return;
      setNodes((ns) =>
        ns.map((n) =>
          n.id === selectedNode.id
            ? {
                ...n,
                data: {
                  ...n.data,
                  assigned_node_id:
                    patch.assigned_node_id !== undefined
                      ? patch.assigned_node_id
                      : n.data.assigned_node_id,
                  ...(patch.params ? { params: patch.params } : {}),
                  ...(patch.arrayed_toggle !== undefined
                    ? { arrayed_toggle: patch.arrayed_toggle }
                    : {}),
                  ...(patch.parallelism !== undefined
                    ? { parallelism: patch.parallelism }
                    : {}),
                },
              }
            : n,
        ),
      );
    },
    [selectedNode, setNodes],
  );

  const onInspectorDelete = useCallback(() => {
    if (!selectedNode) return;
    setNodes((ns) => ns.filter((n) => n.id !== selectedNode.id));
    setEdges((es) =>
      es.filter((e) => e.source !== selectedNode.id && e.target !== selectedNode.id),
    );
  }, [selectedNode, setNodes, setEdges]);

  // --- click a Jobs row → select the corresponding canvas node --------
  const onSelectGraphNode = useCallback(
    (graphNodeId: string) => {
      setNodes((ns) =>
        ns.map((n) => ({ ...n, selected: n.id === graphNodeId })),
      );
    },
    [setNodes],
  );

  // --- graph <-> wire ---------------------------------------------------
  const toGraph = useCallback((): WorkflowGraph => {
    return {
      nodes: nodes.map((n) => {
        const d = n.data as AlgorithmNodeData & {
          params?: Record<string, unknown>;
          arrayed_toggle?: boolean;
          parallelism?: number;
        };
        return {
          id: n.id,
          algorithm_name: d.pack.name,
          algorithm_version: d.pack.version,
          position: { x: n.position.x, y: n.position.y },
          params: d.params ?? {},
          assigned_node_id: d.assigned_node_id,
          // Structural — see docs/pack-spec.md#arrayed-and-arrayable.
          arrayed_toggle: Boolean(d.arrayed_toggle),
          // Structural — bounded parallel shard dispatch (default 1).
          parallelism: Math.max(1, Number(d.parallelism ?? 1)),
          // Cosmetic — persisted with the workflow so it survives
          // refresh + cross-device browsing (the whole point of the
          // "not localStorage" decision).
          preview_open: previewOpenByGraphNode[n.id] ?? null,
        };
      }),
      edges: edges.map((e) => ({
        id: e.id,
        source: e.source,
        sourceHandle: e.sourceHandle ?? "",
        target: e.target,
        targetHandle: e.targetHandle ?? "",
      })),
    };
  }, [nodes, edges, previewOpenByGraphNode]);

  const fromGraph = useCallback(
    (graph: WorkflowGraph): void => {
      // Seed the observer-state map from the graph's own cosmetic
      // fields — this replaces the pre-existing localStorage seed.
      // Any graph node with a stored preview_open opens its drawer on
      // hydration; stale port names are guarded at render time by
      // ``AlgorithmNode`` (``previews?.[previewOpen]``), so a handle
      // that has since been GC'd degrades to a closed drawer.
      setPreviewOpenByGraphNode(() => {
        const next: Record<string, string | null> = {};
        for (const gn of graph.nodes) {
          if (gn.preview_open) next[gn.id] = gn.preview_open;
        }
        return next;
      });
      const hydratedNodes = graph.nodes
        .map((gn) => {
          const pack = catalogByKey.get(`${gn.algorithm_name}@${gn.algorithm_version}`);
          if (!pack) return null;
          const node: Node<AlgorithmNodeData> = {
            id: gn.id,
            type: "algorithm",
            position: gn.position,
            data: {
              pack,
              assigned_node_id: gn.assigned_node_id,
              ...({ params: gn.params } as object),
              ...({ arrayed_toggle: gn.arrayed_toggle ?? false } as object),
              ...({ parallelism: gn.parallelism ?? 1 } as object),
              runtime: runtimeByGraphNode[gn.id],
              previews: previewsByGraphNode[gn.id],
              previewOpen: previewOpenByGraphNode[gn.id] ?? null,
            },
          };
          return node;
        })
        .filter(Boolean) as Node<AlgorithmNodeData>[];
      setNodes(hydratedNodes);
      setEdges(
        graph.edges.map((ge) => ({
          id: ge.id,
          source: ge.source,
          sourceHandle: ge.sourceHandle,
          target: ge.target,
          targetHandle: ge.targetHandle,
          type: "typed",
          data: {},
        })),
      );
      // Defensive re-measure kick — see the useEffect below. We
      // record which node ids just got hydrated so the effect knows
      // to call ``updateNodeInternals`` on them once the DOM has
      // been updated. (Calling it synchronously here would find no
      // matching DOM nodes because the setNodes commit hasn't
      // happened yet.)
      pendingRemeasureRef.current = hydratedNodes.map((n) => n.id);
    },
    [
      catalogByKey,
      setNodes,
      setEdges,
      runtimeByGraphNode,
      previewsByGraphNode,
      previewOpenByGraphNode,
    ],
  );

  // Force xyflow to re-parse handle positions after every hydration.
  //
  // Failure mode this closes off: xyflow's ``adoptUserNodes`` resets
  // ``handleBounds`` to ``undefined`` whenever the userNode identity
  // changes and the userNode has no ``measured`` field yet. During
  // cold-load the sequence is:
  //   1. ``fromGraph`` sets 11 fresh nodes (no measured).
  //   2. The runtime-sync ``useEffect`` fires with drawer-open state
  //      and creates new object identities for the ``preview_open``
  //      nodes (src, fx in the classic STG shape).
  //   3. Both adoptions run before the browser's initial
  //      ResizeObserver tick.
  //   4. RO's tick fires, sets handleBounds on the internal nodes,
  //      applyNodeChanges backfills ``measured`` on the React user
  //      nodes.
  //   5. If step 3 raced ahead of step 4, ``handleBounds`` ends up
  //      undefined on the 9 nodes whose dimensions never changed
  //      (drawer-closed → same 220×N as before → RO doesn't refire),
  //      and edges attached to their handles silently drop out via
  //      ``getEdgePosition``.
  //
  // Reproduction: ~1/30 cold-loads on a 4× CPU-throttled headless
  // Chromium — matches the "经常" (often) intermittency the user
  // reported on the ``classic STG (arrayed)`` workflow (14 edges → 1).
  //
  // The fix: after each fromGraph, explicitly force xyflow to
  // re-parse handles from the DOM. ``useUpdateNodeInternals`` schedules
  // an rAF that queries each node's DOM element and calls the store's
  // ``updateNodeInternals`` — idempotent when handleBounds is already
  // correct, restorative when it's ``undefined``. ``pendingRemeasureRef``
  // itself is declared earlier so the catalog-refresh effect can seed it
  // too when a real pack edit forces identity churn.
  useEffect(() => {
    const ids = pendingRemeasureRef.current;
    if (ids.length === 0) return;
    pendingRemeasureRef.current = [];
    updateNodeInternals(ids);
  }, [nodes, updateNodeInternals]);

  // --- toolbar handlers -------------------------------------------------
  const onSave = useCallback(async () => {
    const result = await saveWorkflow({
      workflow_id: workflowId ?? undefined,
      name: workflowName || "untitled",
      graph: toGraph(),
    });
    setWorkflowId(result.workflow_id);
    window.history.replaceState(null, "", `/w/${encodeURIComponent(result.workflow_id)}`);
  }, [workflowId, workflowName, toGraph]);

  // --- autosave ---------------------------------------------------------
  //
  // Canonical serialised form the autosave hook watches. Includes
  // workflow_name + the full graph (nodes with params + position, plus
  // edges). Position IS included so drag-drops persist, but note we do
  // NOT trigger cosmetic PATCH from here — the cosmetic PATCH path is
  // still available for callers that want to mirror the change to the
  // workflow's latest snapshot (draft view hydration only reads from
  // the snapshot for state badges + previews, not for position).
  //
  // The empty-string sentinel gates the initial mount before the
  // catalog has loaded and the graph has been hydrated; the hook skips
  // saving when it sees "".
  const autosaveSnapshot = useMemo(() => {
    if (catalog.length === 0) return "";
    if (viewingSnapshot) return "";
    return JSON.stringify({ name: workflowName, graph: toGraph() });
  }, [catalog.length, viewingSnapshot, workflowName, toGraph]);

  // Structural diff between in-memory draft and the latest snapshot. Empty
  // when no snapshot exists yet or when the draft matches (structural only —
  // position / preview_open excluded). Passed to RunsPanel to drive both the
  // sentinel row visibility and the "检查" modal content.
  const draftDiff = useMemo<DiffItem[]>(
    () =>
      workflowId !== null && latestSnapshotGraph !== null
        ? diffGraphs(toGraph(), latestSnapshotGraph)
        : [],
    [workflowId, latestSnapshotGraph, toGraph],
  );

  // Per-node result staleness. Rules and rationale live in
  // canvas/staleness.ts — same structural criteria diffGraphs already
  // uses at the workflow scope, so the badge on a node card and the
  // draft-diff sentinel row can't drift out of sync. Kept memoised so
  // the runtime-sync effect below only mints new node identities when
  // a node's staleness actually changes value. Empty {} when no
  // workflow is open — the effect then leaves ``data.staleness``
  // undefined and no badge draws.
  const stalenessByGraphNode = useMemo<Record<string, NodeStaleness | null>>(
    () =>
      workflowId !== null
        ? computeStaleness(toGraph(), latestSnapshotGraph, runtimeByGraphNode)
        : {},
    [workflowId, latestSnapshotGraph, runtimeByGraphNode, toGraph],
  );

  // Push the computed staleness onto each node's data. Kept in a
  // dedicated effect (not folded into the runtime-sync effect above)
  // because ``stalenessByGraphNode`` depends on ``toGraph``, which
  // itself depends on ``nodes`` — putting the compute up-file next to
  // the runtime memo would create a temporal-dead-zone loop.
  // ``stalenessEqual`` prevents identity churn when a value-equal
  // recomputation lands (drag / cosmetic edits re-derive the memo)
  // — otherwise xyflow's ``adoptUserNodes`` would reset handleBounds
  // and edges could disappear during hydration (see CanvasContext.ts
  // for the failure mode).
  useEffect(() => {
    setNodes((current) =>
      current.map((n) => {
        const d = n.data as AlgorithmNodeData;
        const next = stalenessByGraphNode[n.id] ?? null;
        if (stalenessEqual(d.staleness ?? null, next)) return n;
        return { ...n, data: { ...n.data, staleness: next } };
      }),
    );
  }, [stalenessByGraphNode, setNodes]);

  const staleTotal = useMemo(
    () => (viewingSnapshot ? 0 : staleCount(stalenessByGraphNode)),
    [viewingSnapshot, stalenessByGraphNode],
  );

  // Pan/zoom to the earliest topologically-dirty node — the "start
  // rerunning here" jump. Also selects it so the inspector opens on
  // that node and the accent border highlights the card. See
  // canvas/staleness.ts::earliestDirtyId for the selection rule
  // (self_dirty with no self_dirty ancestor).
  const onLocateEarliestStale = useCallback(() => {
    if (!rfInstance) return;
    const target = earliestDirtyId(toGraph(), stalenessByGraphNode);
    if (!target) return;
    const nd = rfInstance.getNode(target);
    if (!nd) return;
    const width = (nd.measured as { width?: number } | undefined)?.width ?? 220;
    const height = (nd.measured as { height?: number } | undefined)?.height ?? 100;
    const cx = nd.position.x + width / 2;
    const cy = nd.position.y + height / 2;
    const zoom = Math.max(0.75, rfInstance.getZoom());
    rfInstance.setCenter(cx, cy, { zoom, duration: 400 });
    setNodes((cur) => cur.map((n) => ({ ...n, selected: n.id === target })));
  }, [rfInstance, stalenessByGraphNode, toGraph, setNodes]);

  const autosave = useDraftAutosave({
    serialisedSnapshot: autosaveSnapshot,
    enabled: !viewingSnapshot,
    save: async () => {
      const result = await saveWorkflow({
        workflow_id: workflowId ?? undefined,
        name: workflowName || "untitled",
        graph: toGraph(),
      });
      return { workflow_id: result.workflow_id };
    },
    beaconBody: () => ({
      url: "/api/workflows",
      body: JSON.stringify({
        workflow_id: workflowId ?? undefined,
        name: workflowName || "untitled",
        graph: toGraph(),
      }),
    }),
    onSaved: (wid) => {
      if (workflowId !== wid) {
        setWorkflowId(wid);
        window.history.replaceState(null, "", `/w/${encodeURIComponent(wid)}`);
      }
    },
  });

  const onRun = useCallback(async () => {
    if (!workflowId) {
      await onSave();
    }
    setRunning(true);
    try {
      const wid =
        workflowId ??
        (
          await saveWorkflow({
            workflow_id: undefined,
            name: workflowName || "untitled",
            graph: toGraph(),
          })
        ).workflow_id;
      setWorkflowId(wid);
      const runResult = await runWorkflow(wid);
      // Snapshot was just created from the current draft. Fetch the
      // frozen graph so the draft-modified badge clears immediately
      // after a successful run.
      void getSnapshot(runResult.snapshot_id).then((snap) => {
        setLatestSnapshotGraph(snap.graph);
        setLatestSnapshotId(runResult.snapshot_id);
      }).catch(() => {/* best-effort; hydration on next load will fix */});
    } catch (e) {
      if (
        e instanceof ApiError &&
        typeof e.detail === "object" &&
        e.detail &&
        "issues" in (e.detail as object)
      ) {
        const issues = (
          e.detail as { issues: Array<{ where: string; message: string }> }
        ).issues;
        alert(
          `Cannot run:\n\n${issues.map((i) => `  - ${i.where}: ${i.message}`).join("\n")}`,
        );
      }
      throw e;
    } finally {
      window.setTimeout(() => setRunning(false), 800);
    }
  }, [workflowId, workflowName, toGraph, onSave]);

  // "New workflow" is now a Gallery affordance; the toolbar navigates
  // back to the Gallery via onExitToGallery instead of clearing state
  // in-place.


  const onOpenSnapshot = useCallback(async (snapshotId: string) => {
    setSnapshotError(null);
    try {
      const snap = await getSnapshot(snapshotId);
      setViewingSnapshot(snap);
      setSnapshotSelectedGraphNodeId(null);
      setSnapshotSelectedEdgeId(null);
      // The Runs section lives in the right sidebar now, not on the
      // canvas — no reason to hide it on open. Keeping it visible also
      // shows the ``currentSnapshotId`` highlight so the user always
      // knows which run they're looking at.
    } catch (e) {
      const msg = e instanceof ApiError ? `HTTP ${e.status}` : (e as Error).message;
      setSnapshotError(`could not open run: ${msg}`);
    }
  }, []);

  const onExitSnapshot = useCallback(() => {
    setViewingSnapshot(null);
    setSnapshotSelectedGraphNodeId(null);
    setSnapshotSelectedEdgeId(null);
  }, []);

  const onRerunFromHere = useCallback(
    async (graphNodeId: string) => {
      if (!viewingSnapshot) return;
      try {
        const result = await rerunFromNode(viewingSnapshot.snapshot_id, graphNodeId);
        // Jump straight to viewing the new snapshot — the user is
        // watching for their re-run's progress. The old snapshot is
        // still there in Runs history if they need to reference it.
        void onOpenSnapshot(result.new_snapshot_id);
      } catch (e) {
        const detail =
          e instanceof ApiError && typeof e.detail === "object" && e.detail
            ? (e.detail as { detail?: string }).detail
            : null;
        throw new Error(detail ?? (e as Error).message);
      }
    },
    [viewingSnapshot, onOpenSnapshot],
  );

  const onLoad = useCallback(
    async (id: string) => {
      // Flip hydrating BEFORE fromGraph so the very first render that
      // opens persisted ``preview_open`` drawers sees the flag set —
      // otherwise the initial paint (with empty runtime/previews) would
      // still flash the terminal placeholders for one frame.
      setHydrating(true);
      const w = await getWorkflow(id);
      setWorkflowId(w.workflow_id);
      setWorkflowName(w.name);
      fromGraph(w.graph);
      setLatestSnapshotGraph(null);
      setLatestSnapshotId(null); // both reset until hydration fills them in
      window.history.replaceState(null, "", `/w/${encodeURIComponent(w.workflow_id)}`);

      // Draft-view "carry the last run" hydration (ComfyUI-style):
      // seed ``latestSnapshotId`` (→ jobs via the sync useEffect) and
      // ``previewsByGraphNode`` from the workflow's snapshots so
      // opening a workflow with prior runs shows the state badge +
      // preview caret without having to look at the snapshot view.
      // Live WS ``job_update`` frames still take precedence — this is
      // strictly a cold-start hydration.
      //
      // Fork/Continue awareness (previews only): the newest snapshot may
      // be a Fork that only holds the freshly-produced graph node — its
      // parent still has the not-forked slots filled. We walk newest →
      // older via ``listWorkflowRuns`` (sorted newest-first) and greedily
      // fill each (graph_node_id, port) preview from the freshest ``done``
      // attribution. Node RUNTIME (dot colour) is NOT unioned across
      // snapshots — see ``latestSnapshotJobs`` for the snapshot-scoped
      // source of truth. That split fixes the "canvas red but latest
      // run green" bug where a prior failed run overwrote current-done
      // state via last-write-wins on graph_node_id.
      void (async () => {
        try {
          const runs = await listWorkflowRuns(w.workflow_id);
          if (!runs || runs.length === 0) {
            // No prior runs → nothing to hydrate; clear the flag so
            // AlgorithmNode falls through to the terminal "尚未运行"
            // for genuine never-ran nodes.
            setHydrating(false);
            return;
          }

          const perGraphPreviewWork: Array<{
            graphNodeId: string;
            portName: string;
            handleId: string;
          }> = [];
          const perShardPreviewWork: Array<{
            graphNodeId: string;
            portName: string;
            handleId: string;
            elementId: string;
          }> = [];
          const doneFilledGnids = new Set<string>();
          const MAX_RUNS_TO_WALK = 8;
          let seenFirstSnap = false;
          for (const run of runs.slice(0, MAX_RUNS_TO_WALK)) {
            const snap = await getSnapshot(run.snapshot_id);
            // runs is newest-first; the very first snapshot we fetch is
            // the latest one — its graph drives the draft-modified badge
            // and its jobs drive node runtime.
            if (!seenFirstSnap) {
              setLatestSnapshotGraph(snap.graph);
              // Stamp the seed ref BEFORE flipping the id so the sync
              // effect (which fires as soon as ``latestSnapshotId`` changes)
              // reads the seeded id on its first tick and short-circuits.
              // Without this the effect at ~L807 clears seeded jobs and
              // refires ``getSnapshot(latestSnapshotId)`` — the 9th
              // snapshot request in a 8-run walk.
              seededSnapshotIdRef.current = run.snapshot_id;
              setLatestSnapshotId(run.snapshot_id);
              setLatestSnapshotJobs(snap.jobs);
              seenFirstSnap = true;
            }
            // Iterate in ASC (created_ts) order and first-write-wins per
            // graph_node_id. For a fan-out slot the parent job is created
            // *before* its shards and its ``output_handles`` carry the
            // aggregated arrayed<T> handle (each shard carries a per-
            // element slice with the same port_name). ASC + first-wins
            // therefore picks the parent so the drawer sees the
            // aggregate — NestedFrameSequencePreview shows "共 N 组"
            // instead of collapsing to one shard's view. See
            // SnapshotCanvas.jobsByGraphNodeId for the mirror comment.
            //
            // Shards (``parent_job_id`` set) are excluded from the
            // aggregate slot for the same reason as the WS handler
            // above — their handle points at ONE element dir, not the
            // aggregate — and are seeded into the partial-shard slot
            // instead so mid-fanout hydration can drive partial preview.
            for (const job of snap.jobs) {
              const gnid = job.graph_node_id;
              if (!gnid) continue;
              if (job.state !== "done") continue;
              const shardEl = job.shard_element_id ?? null;
              const isShard = job.parent_job_id != null && shardEl != null;
              if (isShard) {
                for (const [portName, handleId] of Object.entries(
                  job.output_handles ?? {},
                )) {
                  perShardPreviewWork.push({
                    graphNodeId: gnid,
                    portName,
                    handleId,
                    elementId: shardEl,
                  });
                }
                continue;
              }
              if (doneFilledGnids.has(gnid)) continue;
              doneFilledGnids.add(gnid);
              for (const [portName, handleId] of Object.entries(
                job.output_handles ?? {},
              )) {
                perGraphPreviewWork.push({
                  graphNodeId: gnid,
                  portName,
                  handleId,
                });
              }
            }
          }
          if (perGraphPreviewWork.length > 0) {
            const resolved = await Promise.all(
              perGraphPreviewWork.map(async (item) => {
                try {
                  const info = await getHandle(item.handleId);
                  return { ...item, info };
                } catch {
                  return null;
                }
              }),
            );
            const targets: Record<string, Record<string, PreviewTarget>> = {};
            for (const r of resolved) {
              if (!r) continue;
              const bucket = (targets[r.graphNodeId] ??= {});
              bucket[r.portName] = {
                port_name: r.portName,
                handle_id: r.info.handle_id,
                node_id: r.info.node_id,
                proxy_url: r.info.proxy_url,
                storage: r.info.storage as "dir" | "file",
                absolute_path: r.info.absolute_path,
                deleted: r.info.deleted_ts !== null,
                tags: r.info.tags,
                preview: r.info.preview ?? null,
                dim_labels: r.info.dim_labels ?? null,
                dim_sizes: r.info.dim_sizes ?? null,
              };
            }
            setPreviewsByGraphNode((prev) => {
              const next: Record<
                string,
                Record<string, PreviewTarget>
              > = { ...targets };
              for (const [k, v] of Object.entries(prev)) {
                next[k] = { ...(next[k] ?? {}), ...v };
              }
              return next;
            });
          }
          if (perShardPreviewWork.length > 0) {
            const resolvedShards = await Promise.all(
              perShardPreviewWork.map(async (item) => {
                try {
                  const info = await getHandle(item.handleId);
                  return { ...item, info };
                } catch {
                  return null;
                }
              }),
            );
            const shardTargets: Record<
              string,
              Record<string, PreviewShard[]>
            > = {};
            for (const r of resolvedShards) {
              if (!r) continue;
              const gn = (shardTargets[r.graphNodeId] ??= {});
              const port = (gn[r.portName] ??= []);
              if (port.some((e) => e.element_id === r.elementId)) continue;
              port.push({
                element_id: r.elementId,
                handle_id: r.info.handle_id,
                proxy_url: r.info.proxy_url,
                storage: r.info.storage as "dir" | "file",
              });
            }
            for (const gn of Object.values(shardTargets)) {
              for (const port of Object.values(gn)) {
                port.sort((a, b) =>
                  a.element_id.localeCompare(b.element_id, undefined, {
                    numeric: true,
                    sensitivity: "base",
                  }),
                );
              }
            }
            setPreviewShardsByGraphNode((prev) => {
              const next: Record<
                string,
                Record<string, PreviewShard[]>
              > = { ...shardTargets };
              for (const [k, v] of Object.entries(prev)) {
                next[k] = { ...(next[k] ?? {}), ...v };
              }
              return next;
            });
          }
        } catch (err) {
          console.warn("draft-view hydration failed", err);
        } finally {
          // Terminal verdicts (尚未运行 / 产物已被清理) are now safe to
          // show: either all handles resolved into ``previews`` or the
          // batch failed and we've logged it. The AlgorithmNode-level
          // ``runState === "done" && !target`` check still covers the
          // per-node WS window post-hydration.
          setHydrating(false);
        }
      })();
    },
    [fromGraph],
  );

  // Load the workflow the router handed us. Runs once per initialWorkflowId
  // change and waits for the catalog so ``fromGraph`` has the packs it
  // needs to hydrate each node. A stale/unknown id surfaces via the
  // getWorkflow throw; we just log and leave the canvas blank so the
  // user can hit "← workflows" to get out.
  useEffect(() => {
    if (catalog.length === 0) return;
    if (initialWorkflowId === workflowId) return;
    void onLoad(initialWorkflowId).catch((err) => {
      console.warn("could not open workflow", initialWorkflowId, err);
    });
    // onLoad is stable enough for our purposes; catalog-length gate above
    // gives us the "wait for packs" behaviour. reconnectTick makes the
    // cold-load throw retry when the network comes back after a longer
    // outage than ``resilientFetch``'s inline retry window covered.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [catalog, initialWorkflowId, reconnectTick]);

  const isMobile = useIsMobilePortrait();

  // Slot subtrees — shared by the desktop grid and the mobile tab shell
  // so the two layouts render the exact same panel components (no cloned
  // mobile variants). The desktop grid still owns its splitters and grid
  // areas; the mobile shell just consumes these five slots verbatim.
  const topbarSlot = (
    <WorkflowToolbar
      workflowId={workflowId}
      name={workflowName}
      connected={connected}
      running={running}
      summary={workflowId ? runSummary : null}
      onNameChange={setWorkflowName}
      onRun={onRun}
      onExitToGallery={onExitToGallery}
      saveStatus={autosave.status}
      onSaveRetry={autosave.save}
      staleCount={staleTotal}
      onLocateEarliestStale={onLocateEarliestStale}
    />
  );

  const canvasContentSlot = (
    <>
      <CanvasContext.Provider value={canvasContextValue}>
        {viewingSnapshot ? (
          <>
            <SnapshotBanner
              snapshot={viewingSnapshot}
              onBack={onExitSnapshot}
              onRestored={() => {
                if (workflowId) void onLoad(workflowId);
                onExitSnapshot();
              }}
            />
            <SnapshotCanvas
              snapshot={viewingSnapshot}
              catalog={catalog}
              selectedGraphNodeId={snapshotSelectedGraphNodeId}
              onSelectionChange={setSnapshotSelectedGraphNodeId}
              selectedEdgeId={snapshotSelectedEdgeId}
              onEdgeSelectionChange={setSnapshotSelectedEdgeId}
            />
          </>
        ) : (
          <ReactFlow
            nodes={nodes}
            edges={displayEdges}
            nodeTypes={NODE_TYPES}
            edgeTypes={edgeTypes}
            onNodesChange={onNodesChange as (c: NodeChange[]) => void}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onInit={setRfInstance}
            fitView
            minZoom={dynamicMinZoom}
            proOptions={{ hideAttribution: true }}
          >
            <Controls />
            <Panel
              position="bottom-right"
              style={{ marginBottom: minimapOpen ? 158 : 0 }}
            >
              <MinimapToggleButton
                open={minimapOpen}
                onToggle={() => setMinimapOpen((o) => !o)}
              />
            </Panel>
            {minimapOpen && <MiniMap pannable />}
            <Background gap={20} color="var(--rf-grid)" />
          </ReactFlow>
        )}
      </CanvasContext.Provider>
      {snapshotError && (
        <div
          style={{
            position: "absolute",
            top: 12,
            left: "50%",
            transform: "translateX(-50%)",
            background: "var(--error)",
            color: "var(--accent-fg)",
            padding: "6px 12px",
            borderRadius: "var(--radius-sm)",
            fontSize: "var(--fs-xs)",
            boxShadow: "var(--shadow-2)",
            zIndex: 10,
          }}
        >
          {snapshotError}
        </div>
      )}
    </>
  );

  const packsSlot = <PackPalette catalog={catalog} onRefresh={refreshCatalog} />;
  const nodesSlot = <ComputeNodesPanel nodes={computeNodes} />;
  const runsSlot = workflowId ? (
    <RunsPanel
      workflowId={workflowId}
      onOpenSnapshot={(sid) => void onOpenSnapshot(sid)}
      currentSnapshotId={viewingSnapshot?.snapshot_id ?? latestSnapshotId}
      draftDiff={draftDiff}
      refreshSignal={runsPanelRefreshToken}
      onSnapshotDeleted={(sid) => {
        if (viewingSnapshot?.snapshot_id === sid) {
          onExitSnapshot();
        }
      }}
    />
  ) : null;
  const jobsSlot = (
    <RecentJobsPanel
      jobs={workflowJobs}
      onSelectGraphNode={onSelectGraphNode}
      currentWorkflowId={workflowId}
    />
  );
  const inspectorSlot = edgeInspectorProps ? (
    <EdgeInspector {...edgeInspectorProps} />
  ) : viewingSnapshot ? (
    <SnapshotNodeInspector
      graphNodeId={snapshotSelectedGraphNodeId}
      pack={snapshotSelectedPack}
      job={snapshotSelectedJob}
      onRerunFromHere={onRerunFromHere}
    />
  ) : !selectedNode ? (
    // Nothing selected on the draft canvas — show live server pulse
    // instead of a blank placeholder so the operator can gauge cluster
    // load while planning the next run.
    <NodeMetricsPanel nodes={computeNodes} metricsByNode={metricsByNode} />
  ) : (
    <NodeInspector
      selected={
        selectedNode
          ? {
              id: selectedNode.id,
              algorithm_name: selectedNode.data.pack.name,
              algorithm_version: selectedNode.data.pack.version,
              position: {
                x: selectedNode.position.x,
                y: selectedNode.position.y,
              },
              params:
                (
                  selectedNode.data as AlgorithmNodeData & {
                    params?: Record<string, unknown>;
                  }
                ).params ?? {},
              assigned_node_id: selectedNode.data.assigned_node_id,
              arrayed_toggle: Boolean(
                (
                  selectedNode.data as AlgorithmNodeData & {
                    arrayed_toggle?: boolean;
                  }
                ).arrayed_toggle,
              ),
              parallelism: Math.max(
                1,
                Number(
                  (
                    selectedNode.data as AlgorithmNodeData & {
                      parallelism?: number;
                    }
                  ).parallelism ?? 1,
                ),
              ),
            }
          : null
      }
      pack={selectedNode ? selectedNode.data.pack : null}
      computeNodes={computeNodes}
      onChange={onInspectorChange}
      onDelete={onInspectorDelete}
    />
  );

  // Mobile portrait: swap to the tab shell. Above the 900px breakpoint
  // ``isMobile`` is always false so this branch is dead code for
  // desktop — no CSS or grid changes leak into the wide layout.
  if (isMobile) {
    const mobileSelectionId =
      viewingSnapshot
        ? snapshotSelectedGraphNodeId ?? snapshotSelectedEdgeId ?? null
        : selectedNode?.id ?? selectedEdge?.id ?? null;
    return (
      <MobileShell
        topbar={topbarSlot}
        canvas={
          <div
            ref={rfWrapper}
            onDragOver={onDragOver}
            onDrop={onDrop}
            style={{ position: "absolute", inset: 0 }}
          >
            {canvasContentSlot}
          </div>
        }
        packs={packsSlot}
        nodes={nodesSlot}
        runs={runsSlot}
        jobs={jobsSlot}
        inspect={inspectorSlot}
        selectionId={mobileSelectionId}
        latestSnapshotId={latestSnapshotId}
      />
    );
  }

  return (
    <div
      className="hl-app-shell hl-workspace"
      style={{
        display: "grid",
        gridTemplateRows: `auto minmax(0, 1fr) 6px ${bottomDock.value}px`,
        gridTemplateColumns: `${leftDock.value}px 6px minmax(0, 1fr) 6px ${rightDock.value}px`,
        gridTemplateAreas: `
          "top top top top top"
          "left left-split mid right-split right"
          "bottom-split bottom-split bottom-split bottom-split bottom-split"
          "bottom bottom bottom bottom bottom"
        `,
        height: "100vh",
        background: "var(--bg)",
        color: "var(--text-body)",
      }}
    >
      <div className="hl-topbar" style={{ gridArea: "top" }}>
        {topbarSlot}
      </div>

      <aside
        className="hl-panel"
        style={{
          gridArea: "left",
          borderRight: "none",
          background: "var(--surface)",
          overflow: "hidden",
        }}
      >
        {packsSlot}
      </aside>

      <div style={{ gridArea: "left-split", minHeight: 0 }}>
        <Splitter
          label="Resize packs panel"
          onResize={leftDock.resize}
          onReset={leftDock.reset}
          orientation="vertical"
        />
      </div>

      <main
        ref={rfWrapper}
        onDragOver={onDragOver}
        onDrop={onDrop}
        style={{ gridArea: "mid", position: "relative", overflow: "hidden" }}
      >
        {canvasContentSlot}
      </main>

      <div style={{ gridArea: "right-split", minHeight: 0 }}>
        <Splitter
          label="Resize compute sidebar"
          onResize={rightDock.resize}
          onReset={rightDock.reset}
          orientation="vertical"
          reverse
        />
      </div>

      {/* Right sidebar: Compute Nodes (top, auto-height) stacked over the
          Runs list (fills the rest with its own scroll). The Runs section
          is toggled from the toolbar — hiding it hands the entire right
          column to Compute Nodes. There's no workflow-agnostic runs view,
          so we also fall back to the nodes-only layout when no workflow
          is loaded (Gallery→open flow). */}
      <aside
        className="hl-panel"
        style={{
          gridArea: "right",
          border: "1px solid var(--border)",
          borderLeft: "none",
          background: "var(--surface)",
          overflow: "hidden",
          display: "grid",
          // Both regions retain a usable minimum and scroll independently.
          // With no workflow there is no run history, so the nodes panel gets
          // the entire column instead.
          gridTemplateRows: workflowId
            ? `${rightSplit.value}px 6px minmax(120px, 1fr)`
            : "1fr",
        }}
      >
        <div style={{ minHeight: 0, overflow: "auto" }}>{nodesSlot}</div>
        {runsSlot && (
          <>
            <Splitter
              label="Resize compute nodes and run history"
              onResize={rightSplit.resize}
              onReset={rightSplit.reset}
              orientation="horizontal"
            />
            <div
              style={{
                minHeight: 0,
                overflow: "hidden",
                display: "flex",
                flexDirection: "column",
              }}
            >
              {runsSlot}
            </div>
          </>
        )}
      </aside>

      <div style={{ gridArea: "bottom-split", minWidth: 0 }}>
        <Splitter
          label="Resize inspector"
          onResize={bottomDock.resize}
          onReset={bottomDock.reset}
          orientation="horizontal"
          reverse
        />
      </div>

      <section
        className="hl-inspector"
        style={{
          gridArea: "bottom",
          borderTop: "none",
          background: "var(--surface)",
          minHeight: 0,
          overflow: "hidden",
          display: "grid",
          gridTemplateColumns: `minmax(240px, 1fr) 6px ${bottomSplit.value}px`,
        }}
      >
        <div style={{ overflow: "hidden" }}>{inspectorSlot}</div>
        <Splitter
          label="Resize inspector and recent jobs"
          onResize={bottomSplit.resize}
          onReset={bottomSplit.reset}
          orientation="vertical"
          reverse
        />
        <div style={{ minWidth: 0, overflow: "hidden" }}>{jobsSlot}</div>
      </section>
    </div>
  );
}

// REST /api/jobs summary → panel row. Keeps the two shapes decoupled so a
// future backend change (e.g. richer job metadata) doesn't force a rewrite.
function jobToRow(j: JobSummary): RecentJobRow {
  const row: RecentJobRow = {
    job_id: j.job_id,
    algorithm_name: j.algorithm_name,
    algorithm_version: j.algorithm_version,
    state: j.state,
    graph_node_id: j.graph_node_id,
    progress: j.progress,
    created_ts: j.created_ts,
    updated_ts: j.updated_ts,
    started_ts: j.started_ts ?? null,
    parent_job_id: j.parent_job_id ?? null,
    shard_element_id: j.shard_element_id ?? null,
    expected_shards: j.expected_shards ?? null,
    fail_reason: j.fail_reason,
  };
  (row as unknown as { workflow_id?: string }).workflow_id = j.workflow_id;
  return row;
}

// Silence "unused" warnings for the useReactFlow hook — we may end up using
// it in a follow-up (e.g. programmatic viewport control) and want it available.
void useReactFlow;
