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
import type { ComputeNode } from "../wire";

export interface CanvasContextValue {
  // The workflow this canvas is rendering (draft or snapshot). Used to
  // form ``hololab://graph-node/<workflow_id>/<graph_node_id>`` refs.
  workflow_id: string | null;
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
}

const DEFAULT: CanvasContextValue = {
  workflow_id: null,
  computeNodesById: {},
  hydrating: false,
};

export const CanvasContext = createContext<CanvasContextValue>(DEFAULT);

export function useCanvasContext(): CanvasContextValue {
  return useContext(CanvasContext);
}
