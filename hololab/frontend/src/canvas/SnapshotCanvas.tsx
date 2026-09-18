// Read-only xyflow canvas that renders a past run's frozen graph.
//
// Same node cards, same edges, same positions as the draft — but
// dragging, connecting, and deletion are disabled at the ReactFlow
// level. Selection is enabled so clicking a node updates the bottom
// SnapshotNodeInspector.
//
// State that differs from the draft canvas:
//   * runtime badges come from the snapshot's jobs (by graph_node_id),
//     not the live WS stream — a done run stays "done" forever.
//   * assigned_node_id is taken from the frozen graph, so if the
//     compute node has since been rotated out, the badge still shows
//     the id that produced this artifact (matches "what did this run
//     look like?").
//   * preview drawers work here too — output_handles from each job are
//     resolved to proxy URLs via /api/handles/{id} on mount, then wired
//     into the same AlgorithmNode expand caret + Preview components the
//     draft canvas uses. Toggle state is local to this component (via
//     the AlgorithmNodeData.onPreviewToggle callback) so it doesn't
//     leak into App's draft-scoped preview state.
//
// If a handle can't be resolved (handle GC'd, node offline, etc.) the
// port is silently omitted from the caret and no drawer is offered.
// Handles that resolve but whose bytes fail to stream still get a
// drawer — each viewer component renders its own load-failed state.

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Background,
  Controls,
  MiniMap,
  Panel,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Edge,
  type Node,
  type NodeChange,
  type NodeMouseHandler,
} from "@xyflow/react";
import type {
  CatalogPack,
  SnapshotDetail,
  SnapshotJob,
} from "../wire";
import { getHandle, patchSnapshotGraphNodeCosmetic } from "../api";
import {
  AlgorithmNode,
  type AlgorithmNodeData,
  type NodeRuntime,
  type PreviewTarget,
} from "./AlgorithmNode";
import { MinimapToggleButton } from "./MinimapToggleButton";

const NODE_TYPES = { algorithm: AlgorithmNode };

export interface SnapshotCanvasProps {
  snapshot: SnapshotDetail;
  catalog: CatalogPack[];
  // Currently-selected graph_node_id (drives the bottom inspector) —
  // owned by App so it can also drive the inspector.
  selectedGraphNodeId: string | null;
  onSelectionChange: (graphNodeId: string | null) => void;
}

export function SnapshotCanvas({
  snapshot,
  catalog,
  selectedGraphNodeId,
  onSelectionChange,
}: SnapshotCanvasProps) {
  // Index the catalog by (name, version) so pack lookup for each snapshot
  // node is O(1). If the pack has been uninstalled since the run we still
  // render the node — just with a stub pack (empty ports) and the real
  // metadata comes from the job's algorithm_name/version fields.
  const catalogByKey = useMemo(() => {
    const m = new Map<string, CatalogPack>();
    for (const p of catalog) m.set(`${p.name}@${p.version}`, p);
    return m;
  }, [catalog]);

  const jobsByGraphNodeId = useMemo(() => {
    const m = new Map<string, SnapshotJob>();
    for (const j of snapshot.jobs) {
      if (j.graph_node_id) m.set(j.graph_node_id, j);
    }
    return m;
  }, [snapshot.jobs]);

  // Local preview state. Resolved on snapshot change from each job's
  // output_handles; toggle state is a plain map. Both are local (not
  // App-scoped) so opening a drawer here doesn't leak into the draft.
  const [previewsByGraphNode, setPreviewsByGraphNode] = useState<
    Record<string, Record<string, PreviewTarget>>
  >({});
  const [minimapOpen, setMinimapOpen] = useState(false);
  const [previewOpenByGraphNode, setPreviewOpenByGraphNode] = useState<
    Record<string, string | null>
  >({});

  useEffect(() => {
    // Reset & re-resolve whenever the user opens a different snapshot.
    // Preview-drawer state comes from each snapshot's own frozen
    // ``graph.nodes[i].preview_open`` — historical snapshots keep the
    // drawer state that was set when they were taken (or updated
    // later via ``PATCH /api/snapshots/{sid}/graph-nodes/{gnid}/
    // cosmetic``), which matches the cosmetic-is-per-snapshot half of
    // the field classification (see docs/workflow-schema.md).
    setPreviewsByGraphNode({});
    const initialOpen: Record<string, string | null> = {};
    for (const gn of snapshot.graph.nodes) {
      const raw = (gn as { preview_open?: string | null }).preview_open;
      if (raw) initialOpen[gn.id] = raw;
    }
    setPreviewOpenByGraphNode(initialOpen);

    let cancelled = false;

    // Only ports that (a) the pack declares as previewable and (b) have
    // a handle in this run are worth resolving. Skipping the rest avoids
    // 404 storms against handles the frontend would never render anyway.
    type Work = {
      graphNodeId: string;
      portName: string;
      handleId: string;
    };
    const work: Work[] = [];
    for (const gn of snapshot.graph.nodes) {
      const pack = catalogByKey.get(`${gn.algorithm_name}@${gn.algorithm_version}`);
      if (!pack) continue;
      const job = jobsByGraphNodeId.get(gn.id);
      const outputs = job?.output_handles;
      if (!outputs) continue;
      for (const [portName, handleId] of Object.entries(outputs)) {
        if (pack.outputs[portName]?.preview) {
          work.push({ graphNodeId: gn.id, portName, handleId });
        }
      }
    }

    if (work.length === 0) return;

    void Promise.all(
      work.map(async (w) => {
        try {
          const info = await getHandle(w.handleId);
          return {
            ok: true as const,
            w,
            target: {
              port_name: w.portName,
              handle_id: info.handle_id,
              node_id: info.node_id,
              proxy_url: info.proxy_url,
              storage: info.storage as "dir" | "file",
              absolute_path: info.absolute_path,
              deleted: info.deleted_ts !== null,
            },
          };
        } catch (err) {
          // Handle GC'd, node offline, permissions — either way the port
          // just won't get an expand caret. Log for diagnostics and move
          // on; other ports on the same run still resolve.
          console.warn(
            "snapshot handle lookup failed",
            { handleId: w.handleId, graphNodeId: w.graphNodeId, port: w.portName },
            err,
          );
          return { ok: false as const, w };
        }
      }),
    ).then((results) => {
      if (cancelled) return;
      const next: Record<string, Record<string, PreviewTarget>> = {};
      for (const r of results) {
        if (!r.ok) continue;
        (next[r.w.graphNodeId] ??= {})[r.w.portName] = r.target;
      }
      setPreviewsByGraphNode(next);
    });

    return () => {
      cancelled = true;
    };
  }, [snapshot, catalogByKey, jobsByGraphNodeId]);

  // Toggle callback threaded onto every node via data — AlgorithmNode
  // detects this and calls it instead of firing the App-level DOM event.
  // Persists cosmetic-only to the snapshot itself so re-opening this
  // run shows the same drawer state (matches cosmetic-per-snapshot in
  // docs/workflow-schema.md).
  const togglePreview = useCallback(
    (graphNodeId: string, portName: string | null) => {
      setPreviewOpenByGraphNode((prev) => ({ ...prev, [graphNodeId]: portName }));
      void patchSnapshotGraphNodeCosmetic(snapshot.snapshot_id, graphNodeId, {
        preview_open: portName,
      }).catch((err) => console.warn("snapshot preview_open patch failed", err));
    },
    [snapshot.snapshot_id],
  );

  // Build the xyflow nodes/edges from the frozen graph. Runtime badge
  // and preview state are baked in per-render — nothing else streams
  // into this canvas.
  const initial = useMemo(() => {
    const rfNodes: Node<AlgorithmNodeData>[] = snapshot.graph.nodes.map((gn) => {
      const pack =
        catalogByKey.get(`${gn.algorithm_name}@${gn.algorithm_version}`) ??
        stubPack(gn.algorithm_name, gn.algorithm_version);
      const job = jobsByGraphNodeId.get(gn.id) ?? null;
      const runtime: NodeRuntime | undefined = job
        ? {
            state: job.state,
            progress: job.progress ?? null,
            fail_reason: job.fail_reason ?? null,
            job_id: job.job_id,
          }
        : undefined;
      return {
        id: gn.id,
        type: "algorithm",
        position: gn.position,
        selected: gn.id === selectedGraphNodeId,
        data: {
          pack,
          assigned_node_id: gn.assigned_node_id,
          runtime,
          previews: previewsByGraphNode[gn.id],
          previewOpen: previewOpenByGraphNode[gn.id] ?? null,
          onPreviewToggle: (portName: string | null) =>
            togglePreview(gn.id, portName),
          // Snapshot view is read-only — "run this node" from a frozen
          // past snapshot has no clean meaning (it would create a new
          // run on the current draft head, not resurrect this snapshot).
          // The Run button in PreviewPlaceholder hides on this flag.
          readOnly: true,
        },
      };
    });
    const rfEdges: Edge[] = snapshot.graph.edges.map((e) => ({
      id: e.id,
      source: e.source,
      sourceHandle: e.sourceHandle,
      target: e.target,
      targetHandle: e.targetHandle,
      style: { strokeWidth: 2 },
    }));
    return { nodes: rfNodes, edges: rfEdges };
    // Selection is applied per-render below, so it's intentionally NOT
    // in the dep list here — otherwise every selection change would
    // rebuild the whole node array. previews/previewOpen ARE deps: they
    // control the expand caret + drawer visibility, so we do want a
    // rebuild when they change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    snapshot,
    catalogByKey,
    jobsByGraphNodeId,
    previewsByGraphNode,
    previewOpenByGraphNode,
    togglePreview,
  ]);

  const [nodes, setNodes, onNodesChange] = useNodesState<Node<AlgorithmNodeData>>(
    initial.nodes,
  );
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(initial.edges);

  // Re-seed when we switch to a different snapshot (or the catalog
  // changes and pack labels need refreshing).
  useEffect(() => {
    setNodes(initial.nodes);
    setEdges(initial.edges);
  }, [initial, setNodes, setEdges]);

  // Reflect the App-owned selection state onto xyflow's per-node
  // ``selected`` flag. This keeps the inspector's selection and the
  // canvas's blue outline in sync even when selection changes come from
  // outside (e.g. clicking a run row that jumps you to a specific node).
  useEffect(() => {
    setNodes((current) =>
      current.map((n) =>
        n.selected === (n.id === selectedGraphNodeId)
          ? n
          : { ...n, selected: n.id === selectedGraphNodeId },
      ),
    );
  }, [selectedGraphNodeId, setNodes]);

  const onNodeClick: NodeMouseHandler = (_evt, node) => {
    onSelectionChange(node.id);
  };
  const onPaneClick = () => onSelectionChange(null);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={NODE_TYPES}
      onNodesChange={onNodesChange as (c: NodeChange[]) => void}
      onEdgesChange={onEdgesChange}
      onNodeClick={onNodeClick}
      onPaneClick={onPaneClick}
      // Read-only affordances: no drag, no connect, no deletion. We
      // still leave elementsSelectable=true (the default) because the
      // whole point of the snapshot view is to inspect nodes.
      nodesDraggable={false}
      nodesConnectable={false}
      edgesFocusable={false}
      deleteKeyCode={null}
      fitView
      proOptions={{ hideAttribution: true }}
    >
      {/* Toggle Panel + MiniMap both anchor bottom-right; when the
          minimap is expanded, lift the button by minimap-height + gap
          so it stays visible/clickable above the map instead of being
          covered by it. */}
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
      <Controls showInteractive={false} />
      <Background gap={20} color="var(--rf-grid)" />
    </ReactFlow>
  );
}

// A pack whose install has since been removed from any online node
// won't show up in the catalog lookup — we still render the node so the
// user can see what ran, just with empty ports and a subtle "unavailable"
// hint via the inspector when they click on it.
function stubPack(name: string, version: string): CatalogPack {
  return {
    name,
    version,
    manifest_hash: "",
    node_ids: [],
    description: null,
    category: [],
    docs: null,
    source_entry: null,
    manifest_path: null,
    source_dir: null,
    inputs: {},
    outputs: {},
    params: {},
  };
}
