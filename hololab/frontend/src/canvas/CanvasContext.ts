// Shared metadata for the algorithm-canvas surface.
//
// Motivation: ``workflow_id`` and the compute-nodes lookup used to live on
// every canvas node's ``data`` field. That made every WS-triggered
// compute-nodes refresh (or a workflow open) recreate all node objects,
// which fed xyflow's ``adoptUserNodes`` a stream of new user-node
// identities. During cold-start hydration — before ResizeObserver had
// measured any node — the re-adoption reset each node's
// ``handleBounds`` to ``undefined`` via ``parseHandles(userNode)``
// (`!userNode.measured` → returns undefined). Edges attached to those
// handles then failed ``getEdgePosition`` and silently disappeared;
// nodes whose dimensions changed (drawer open/close) recovered on the
// next ResizeObserver tick, unchanged-dimension nodes did not.
//
// Passing this metadata through a context instead means (a) the values
// change without touching node identities, and (b) ``AlgorithmNode``
// still sees the same fresh data on every render because context reads
// are always up-to-date.

import { createContext, useContext } from "react";
import type { ComputeNode, VariableDeclarations } from "../wire";
import type { WorkflowViewerView } from "../api";

export interface CanvasContextValue {
  // The workflow this canvas is rendering (draft or snapshot). Used to
  // form ``hololab://graph-node/<workflow_id>/<graph_node_id>`` refs.
  workflow_id: string | null;
  /** Human-readable name used in externally shared node references. */
  workflow_name: string;
  // Compute-node lookup keyed by node_id. Consumed by the preview
  // drawer's "Open in Cocoder" button to resolve
  // ``flops_executor_id`` at render time.
  computeNodesById: Record<string, ComputeNode>;
  // True while the initial cold-load hydration (runs list → snapshot
  // jobs → getHandle batch) is still in flight. AlgorithmNode reads
  // this to suppress the terminal "尚未运行 / 产物已被清理" verdicts
  // until we have enough data to make a real call — otherwise a stale
  // ``preview_open`` slot on a persisted graph would flash both wrong
  // conclusions on every cold load (target=null + runState=undefined
  // → "never-ran"; then jobs land, runState="done" but handles still
  // pending → "cleaned"; then handles land → real preview). See the
  // block comment on the placeholder computation in AlgorithmNode.tsx.
  hydrating: boolean;
  variables?: VariableDeclarations;
  effectiveParamsByNode?: Record<string, Record<string, unknown>>;
  onBindingChange?: (nodeId: string, name: string, variable: string | null) => void;
  pinnedParams?: Record<string, string[]>;
  onParamsChange?: (nodeId: string, patch: Record<string, unknown>) => void;
  onInlineValueChange?: (nodeId: string, port: string, value: unknown) => void;
  onInspectInput?: (nodeId: string, port: string) => void;
  /** The workflow-wide default view, read once by each viewer at open time. */
  viewer_view: WorkflowViewerView | null;
  /** True once the server lookup completed (including an empty default). */
  viewer_view_loaded: boolean;
  /** False when this canvas has no reachable workflow-view service. */
  viewer_view_managed: boolean;
  /** Called only by an explicit “set/reset default view” action. */
  setViewerDefault?: (view: WorkflowViewerView | null) => void;
}

const DEFAULT: CanvasContextValue = {
  workflow_id: null,
  workflow_name: "untitled",
  computeNodesById: {},
  hydrating: false,
  viewer_view: null,
  viewer_view_loaded: false,
  viewer_view_managed: false,
};

export const CanvasContext = createContext<CanvasContextValue>(DEFAULT);

export function useCanvasContext(): CanvasContextValue {
  return useContext(CanvasContext);
}
