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
  type Connection,
  type Edge,
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
  NodeOfflinePayload,
  NodeOnlinePayload,
  SnapshotDetail,
  WorkflowGraph,
} from "./wire";
import {
  ApiError,
  dispatchNode,
  getComputeNodes,
  getHandle,
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
import { tagsCompatible } from "./tags";
import { diffGraphs } from "./canvas/diffGraphs";
import type { DiffItem } from "./canvas/diffGraphs";
import {
  AlgorithmNode,
  PREVIEW_TOGGLE_EVENT,
  RUN_NODE_EVENT,
  type AlgorithmNodeData,
  type NodeRuntime,
  type PreviewTarget,
  type PreviewToggleDetail,
  type RunNodeDetail,
} from "./canvas/AlgorithmNode";
import { Artifacts } from "./Artifacts";
import { Gallery } from "./Gallery";
import { PackPalette } from "./canvas/PackPalette";
import { ComputeNodesPanel } from "./canvas/ComputeNodesPanel";
import { MinimapToggleButton } from "./canvas/MinimapToggleButton";
import { NodeInspector } from "./canvas/NodeInspector";
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

// A workflow-scoped id counter, distinct from xyflow's internal instance ids.
let ID_COUNTER = 1;
function mintId(prefix: string): string {
  return `${prefix}${Date.now().toString(36)}${(ID_COUNTER++).toString(36)}`;
}

// Tiny hash-router: three top-level screens.
//   "gallery"          — landing page (route `/` or empty hash)
//   "artifacts"        — cleanup / inventory page (`#artifacts`)
//   { workflowId: id } — canvas view for one workflow (`#w={id}`)
// No router library — a hashchange listener + a switch is clearer than
// pulling in react-router for three states.
type Route =
  | { kind: "gallery" }
  | { kind: "artifacts" }
  | { kind: "canvas"; workflowId: string };

function parseRoute(): Route {
  const hash = window.location.hash;
  if (hash.startsWith("#artifacts")) return { kind: "artifacts" };
  const m = /#w=([^&]+)/.exec(hash);
  if (m) return { kind: "canvas", workflowId: decodeURIComponent(m[1]) };
  return { kind: "gallery" };
}

function navigateToGallery(): void {
  // Clear the hash without reloading. Some browsers treat a bare "#"
  // as still having a hash — set to "" via history.replaceState so the
  // URL stays clean.
  window.history.pushState(null, "", window.location.pathname);
  // pushState doesn't fire hashchange; manually notify the App router.
  window.dispatchEvent(new HashChangeEvent("hashchange"));
}

function navigateToCanvas(workflowId: string): void {
  window.location.hash = `w=${encodeURIComponent(workflowId)}`;
}

function navigateToArtifacts(): void {
  window.location.hash = "artifacts";
}

export default function App() {
  const [route, setRoute] = useState<Route>(() => parseRoute());
  useEffect(() => {
    const onHash = () => setRoute(parseRoute());
    window.addEventListener("hashchange", onHash);
    window.addEventListener("popstate", onHash);
    return () => {
      window.removeEventListener("hashchange", onHash);
      window.removeEventListener("popstate", onHash);
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

  // --- catalog + compute nodes -------------------------------------------
  const [catalog, setCatalog] = useState<CatalogPack[]>([]);
  const [computeNodes, setComputeNodes] = useState<ComputeNode[]>([]);
  const [connected, setConnected] = useState(false);
  const [running, setRunning] = useState(false);

  // Two indices over job runtime state:
  //   - runtimeByGraphNode:  graph_node_id → latest NodeRuntime
  //     (drives the coloured badge on each canvas node)
  //   - jobsById:            job_id → RecentJobRow
  //     (drives the RecentJobsPanel + the workflow-run summary chip)
  const [runtimeByGraphNode, setRuntimeByGraphNode] = useState<
    Record<string, NodeRuntime>
  >({});
  const [jobsById, setJobsById] = useState<Record<string, RecentJobRow>>({});

  // Preview state, keyed by graph node id:
  //   * previewsByGraphNode: resolved proxy URLs for each output port that
  //     produced a handle AND has a preview declaration.
  //   * previewOpenByGraphNode: which drawer is currently expanded per node
  //     (null / missing = collapsed).
  const [previewsByGraphNode, setPreviewsByGraphNode] = useState<
    Record<string, Record<string, PreviewTarget>>
  >({});
  // Held on a ref so the PREVIEW_TOGGLE_EVENT listener can read the
  // *current* workflow id when persisting a user toggle, without
  // having to re-register the listener each time workflowId changes.
  // Set below in the workflowId useEffect.
  const workflowIdRef = useRef<string | null>(null);
  const [previewOpenByGraphNode, setPreviewOpenByGraphNode] = useState<
    Record<string, string | null>
  >({});

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
  }, [refreshCatalog]);

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
        // Also seed per-graph-node runtime for anything workflow-scoped.
        setRuntimeByGraphNode((prev) => {
          const next = { ...prev };
          for (const j of summaries) {
            if (j.graph_node_id) {
              next[j.graph_node_id] = {
                state: j.state,
                progress: j.progress,
                fail_reason: j.fail_reason,
                job_id: j.job_id,
              };
            }
          }
          return next;
        });
      })
      .catch(() => {
        /* ignore — first paint keeps working with empty state */
      });
  }, []);

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
      };
      ws.onclose = () => {
        setConnected(false);
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
          };
          // Preserve workflow_id (carried on the wire but not on the panel row shape).
          (next as unknown as { workflow_id?: string }).workflow_id =
            (existing as unknown as { workflow_id?: string })?.workflow_id ??
            p.workflow_id;
          return { ...prev, [p.job_id]: next };
        });

        // Map onto the canvas node via graph_node_id.
        if (p.graph_node_id) {
          setRuntimeByGraphNode((prev) => ({
            ...prev,
            [p.graph_node_id!]: {
              state: p.state,
              progress: p.progress ?? null,
              fail_reason: p.fail?.reason ?? null,
              job_id: p.job_id,
            },
          }));
        }

        // On the terminal transition to done, the gateway includes
        // output_handles in the frame. Resolve each to a preview target
        // (proxy URL + storage form) via /api/handles/{id}. The result
        // powers the in-canvas expand drawer for viewer-declared ports.
        if (p.state === "done" && p.graph_node_id && p.output_handles) {
          const gnid = p.graph_node_id;
          const handles = p.output_handles;
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
  const [nodes, setNodes, onNodesChange] = useNodesState<Node<AlgorithmNodeData>>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  // Frozen graph from the most-recent snapshot. Null when no snapshot exists
  // yet. Used by draftDiff to populate the "草稿有结构改动" sentinel row.
  const [latestSnapshotGraph, setLatestSnapshotGraph] = useState<WorkflowGraph | null>(
    null,
  );

  const catalogByKey = useMemo(() => {
    const m = new Map<string, CatalogPack>();
    for (const p of catalog) m.set(`${p.name}@${p.version}`, p);
    return m;
  }, [catalog]);

  // Refresh AlgorithmNode.data.pack when the catalog changes so signatures
  // stay accurate after a pack edit.
  useEffect(() => {
    setNodes((current) =>
      current.map((n) => {
        const key = `${n.data.pack.name}@${n.data.pack.version}`;
        const fresh = catalogByKey.get(key);
        return fresh ? { ...n, data: { ...n.data, pack: fresh } } : n;
      }),
    );
  }, [catalogByKey, setNodes]);

  // Push job runtime + resolved previews + workflow_id into each node's
  // data. This drives the status badge, expand caret, inline preview
  // surface, and the card-header ⧉'s graph-node ref (which needs the
  // parent workflow id to form a resolvable token).
  // Map form of the compute-nodes list, kept memoised so the sync
  // effect below can pass identity-stable data down into node.data
  // (avoids re-renders on every catalog poll when nothing changed).
  const computeNodesById = useMemo<Record<string, ComputeNode>>(() => {
    const out: Record<string, ComputeNode> = {};
    for (const cn of computeNodes) out[cn.node_id] = cn;
    return out;
  }, [computeNodes]);

  useEffect(() => {
    setNodes((current) =>
      current.map((n) => {
        const d = n.data as AlgorithmNodeData;
        const rt = runtimeByGraphNode[n.id];
        const pv = previewsByGraphNode[n.id];
        const po = previewOpenByGraphNode[n.id] ?? null;
        const wid = workflowId ?? null;
        const cnbi = computeNodesById;
        if (
          rt === d.runtime &&
          pv === d.previews &&
          po === (d.previewOpen ?? null) &&
          wid === (d.workflow_id ?? null) &&
          cnbi === d.computeNodesById
        ) {
          return n;
        }
        return {
          ...n,
          data: {
            ...n.data,
            runtime: rt,
            previews: pv,
            previewOpen: po,
            workflow_id: wid,
            computeNodesById: cnbi,
          },
        };
      }),
    );
  }, [
    runtimeByGraphNode,
    previewsByGraphNode,
    previewOpenByGraphNode,
    workflowId,
    computeNodesById,
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
      const { graph_node_id, resolve, reject } = (
        evt as CustomEvent<RunNodeDetail>
      ).detail;
      const wid = workflowIdRef.current;
      if (!wid) {
        reject("save the workflow first (no workflow_id yet)");
        return;
      }
      dispatchNode(wid, graph_node_id).then(
        () => resolve(),
        (err: Error) => reject(err.message || "dispatch failed"),
      );
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

      const srcTags = src.pack.outputs[conn.sourceHandle]?.tags ?? [];
      const tgtTags = tgt.pack.inputs[conn.targetHandle]?.tags ?? [];
      if (!tagsCompatible(srcTags, tgtTags)) {
        // Flash a message via console for MVP; a toast is a follow-up.
        console.warn(
          `edge rejected: tags [${srcTags.join(",")}] × [${tgtTags.join(",")}] have no overlap`,
        );
        return;
      }

      setEdges((es) =>
        addEdge(
          { ...conn, id: mintId("e"), animated: false, style: { strokeWidth: 2 } },
          es,
        ),
      );
    },
    [nodes, setEdges],
  );

  // --- drop target: convert a dragged pack into a canvas node ----------
  const rfWrapper = useRef<HTMLDivElement>(null);
  const [rfInstance, setRfInstance] = useState<ReactFlowInstance | null>(null);

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
        },
      };
      setNodes((ns) => ns.concat(node));
    },
    [rfInstance, catalogByKey, setNodes],
  );

  // --- selection → NodeInspector ---------------------------------------
  const selectedNode = useMemo(() => nodes.find((n) => n.selected) || null, [nodes]);

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
        const d = n.data as AlgorithmNodeData & { params?: Record<string, unknown> };
        return {
          id: n.id,
          algorithm_name: d.pack.name,
          algorithm_version: d.pack.version,
          position: { x: n.position.x, y: n.position.y },
          params: d.params ?? {},
          assigned_node_id: d.assigned_node_id,
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
      setNodes(
        graph.nodes
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
                runtime: runtimeByGraphNode[gn.id],
                previews: previewsByGraphNode[gn.id],
                previewOpen: previewOpenByGraphNode[gn.id] ?? null,
              },
            };
            return node;
          })
          .filter(Boolean) as Node<AlgorithmNodeData>[],
      );
      setEdges(
        graph.edges.map((ge) => ({
          id: ge.id,
          source: ge.source,
          sourceHandle: ge.sourceHandle,
          target: ge.target,
          targetHandle: ge.targetHandle,
          style: { strokeWidth: 2 },
        })),
      );
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

  // --- toolbar handlers -------------------------------------------------
  const onSave = useCallback(async () => {
    const result = await saveWorkflow({
      workflow_id: workflowId ?? undefined,
      name: workflowName || "untitled",
      graph: toGraph(),
    });
    setWorkflowId(result.workflow_id);
    window.history.replaceState(null, "", `#w=${result.workflow_id}`);
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
        window.history.replaceState(null, "", `#w=${wid}`);
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
      void getSnapshot(runResult.snapshot_id).then(
        (snap) => setLatestSnapshotGraph(snap.graph),
      ).catch(() => {/* best-effort; hydration on next load will fix */});
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
      const w = await getWorkflow(id);
      setWorkflowId(w.workflow_id);
      setWorkflowName(w.name);
      fromGraph(w.graph);
      setLatestSnapshotGraph(null); // reset until hydration fills it in
      window.history.replaceState(null, "", `#w=${w.workflow_id}`);

      // Draft-view "carry the last run" hydration (ComfyUI-style):
      // seed ``runtimeByGraphNode`` and ``previewsByGraphNode`` from
      // the workflow's most recent snapshot so opening a workflow that
      // has a prior run shows the state badge + preview caret on each
      // node without having to look at the snapshot view.
      // Live WS ``job_update`` frames still take precedence — this is
      // strictly a cold-start hydration.
      //
      // Fork/Continue awareness: the newest snapshot may be a Fork
      // that only holds the freshly-produced graph node — its parent
      // snapshot still has the not-forked slots filled. We walk newest
      // → older by consulting ``listWorkflowRuns`` (already sorted
      // newest-first) and greedily fill each graph_node_id's first
      // hit. This mirrors "show the most recent artifact at each
      // canvas slot" without needing a parent chain walker per row.
      void (async () => {
        try {
          const runs = await listWorkflowRuns(w.workflow_id);
          if (!runs || runs.length === 0) return;

          const nextRuntime: Record<string, NodeRuntime> = {};
          const perGraphPreviewWork: Array<{
            graphNodeId: string;
            portName: string;
            handleId: string;
          }> = [];
          // Union across runs newest-first. For each graph_node_id we
          // pick the freshest ``done`` attribution we can find (so a
          // failed retry on the newest snapshot doesn't hide an
          // earlier successful run's preview). Cap the number of runs
          // we visit so pathological workflows with hundreds of
          // snapshots don't spam the API on open.
          const doneFilledGnids = new Set<string>();
          const anyFilledGnids = new Set<string>();
          const MAX_RUNS_TO_WALK = 8;
          let seenFirstSnap = false;
          for (const run of runs.slice(0, MAX_RUNS_TO_WALK)) {
            const snap = await getSnapshot(run.snapshot_id);
            // runs is newest-first; the very first snapshot we fetch is
            // the latest one — store its graph for the draft-modified badge.
            if (!seenFirstSnap) {
              setLatestSnapshotGraph(snap.graph);
              seenFirstSnap = true;
            }
            // Inside one snapshot the jobs come oldest-first; iterate
            // newest-first so a later done attempt at the same slot
            // wins over an earlier failed one.
            for (const job of [...snap.jobs].reverse()) {
              const gnid = job.graph_node_id;
              if (!gnid) continue;
              const isDone = job.state === "done";
              // Skip if we've already filled with a done attribution
              // — done wins forever once we have one. A non-done slot
              // gets upgraded if a done job for the same gnid shows
              // up later in the walk.
              if (doneFilledGnids.has(gnid)) continue;
              if (anyFilledGnids.has(gnid) && !isDone) continue;
              anyFilledGnids.add(gnid);
              if (isDone) doneFilledGnids.add(gnid);
              nextRuntime[gnid] = {
                state: job.state,
                progress: job.progress ?? null,
                fail_reason: job.fail_reason ?? null,
                job_id: job.job_id,
              };
              // Only a done job carries preview-worthy output_handles;
              // failed / cancelled slots stay preview-less.
              if (isDone) {
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
          }
          if (Object.keys(nextRuntime).length > 0) {
            // Merge on top of anything the live-jobs seed may have
            // written already — live wins if it exists, this fills
            // the gaps the live feed doesn't cover for a stale run.
            setRuntimeByGraphNode((prev) => {
              const next = { ...nextRuntime };
              for (const [k, v] of Object.entries(prev)) {
                if (v) next[k] = v;
              }
              return next;
            });
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
        } catch (err) {
          console.warn("draft-view hydration failed", err);
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
    // gives us the "wait for packs" behaviour.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [catalog, initialWorkflowId]);

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
        />
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
        <PackPalette catalog={catalog} onRefresh={refreshCatalog} />
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
        {viewingSnapshot ? (
          <>
            <SnapshotBanner
              snapshot={viewingSnapshot}
              onBack={onExitSnapshot}
              onRestored={() => {
                // After restoring: reload the draft AND exit snapshot
                // mode so the user lands directly on the freshly-cloned
                // editable graph.
                if (workflowId) void onLoad(workflowId);
                onExitSnapshot();
              }}
            />
            <SnapshotCanvas
              snapshot={viewingSnapshot}
              catalog={catalog}
              selectedGraphNodeId={snapshotSelectedGraphNodeId}
              onSelectionChange={setSnapshotSelectedGraphNodeId}
              computeNodesById={computeNodesById}
            />
          </>
        ) : (
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={NODE_TYPES}
            onNodesChange={onNodesChange as (c: NodeChange[]) => void}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onInit={setRfInstance}
            fitView
            proOptions={{ hideAttribution: true }}
          >
            <Controls />
            {/* The MiniMap (also position=bottom-right, ~150px tall) would
                otherwise sit on top of this button and hide the very
                control the user needs to collapse it. When open, lift
                the toggle Panel by minimap-height + gap so it stacks
                cleanly above the map. */}
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
        <div style={{ minHeight: 0, overflow: "auto" }}>
          <ComputeNodesPanel nodes={computeNodes} />
        </div>
        {workflowId && (
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
            <RunsPanel
              workflowId={workflowId}
              onOpenSnapshot={(sid) => void onOpenSnapshot(sid)}
              currentSnapshotId={viewingSnapshot?.snapshot_id ?? null}
              draftDiff={draftDiff}
            />
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
        <div style={{ overflow: "hidden" }}>
          {viewingSnapshot ? (
            <SnapshotNodeInspector
              graphNodeId={snapshotSelectedGraphNodeId}
              pack={snapshotSelectedPack}
              job={snapshotSelectedJob}
              onRerunFromHere={onRerunFromHere}
            />
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
                    }
                  : null
              }
              pack={selectedNode ? selectedNode.data.pack : null}
              computeNodes={computeNodes}
              onChange={onInspectorChange}
              onDelete={onInspectorDelete}
            />
          )}
        </div>
        <Splitter
          label="Resize inspector and recent jobs"
          onResize={bottomSplit.resize}
          onReset={bottomSplit.reset}
          orientation="vertical"
          reverse
        />
        <div style={{ minWidth: 0, overflow: "hidden" }}>
          <RecentJobsPanel
            jobs={workflowJobs}
            onSelectGraphNode={onSelectGraphNode}
            currentWorkflowId={workflowId}
          />
        </div>
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
    fail_reason: j.fail_reason,
  };
  (row as unknown as { workflow_id?: string }).workflow_id = j.workflow_id;
  return row;
}

// Silence "unused" warnings for the useReactFlow hook — we may end up using
// it in a follow-up (e.g. programmatic viewport control) and want it available.
void useReactFlow;
