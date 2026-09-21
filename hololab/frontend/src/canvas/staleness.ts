// Per-node result staleness against the latest snapshot.
//
// A node is "stale" when clicking Run right now would materially change
// its output relative to what the latest snapshot has stored. The
// criteria mirror the structural fields diffGraphs.ts already reports
// at the workflow level (algorithm_name/version, params, assigned node,
// arrayed_toggle, parallelism, inbound edges) so this module and
// diffGraphs stay a single source of truth for "changed vs snapshot".
//
// The same criteria also match backend Continue/Fork semantics: any
// structural change on a slot that already has a job → dispatch_graph_node
// forks a new snapshot; unchanged structural fields on an existing slot
// → Continue. See gateway/execution.py::dispatch_graph_node.
//
// Two kinds of staleness are surfaced:
//
//   self_dirty      — this node's own config diverged from the snapshot,
//                     OR the snapshot has no fresh output for it (never
//                     ran, failed, cancelled, orphaned, or brand-new).
//   upstream_dirty  — this node itself matches the snapshot and has a
//                     ``done`` output, but at least one ancestor is
//                     self_dirty/upstream_dirty. The output is built
//                     on now-outdated inputs.
//
// Nodes currently in-flight (pending/assigned/running) are neither —
// their existing status dot already tells the whole story; a badge on
// top would just be noise. Same for the snapshot canvas: it never
// receives ``staleness`` in its node data (SnapshotCanvas builds nodes
// independently), and AlgorithmNode double-guards on ``readOnly``.

import type { GraphEdge, GraphNode, WorkflowGraph } from "../wire";
import type { NodeRuntime } from "./AlgorithmNode";

const IN_FLIGHT = new Set(["pending", "assigned", "running"]);

export interface NodeStaleness {
  kind: "self_dirty" | "upstream_dirty";
  // Human-readable one-line explanation for the ``title`` tooltip on
  // the badge. For self_dirty this enumerates the actual changes
  // (params, algorithm version, edges, …) so the operator can tell
  // which edit forces a rerun.
  title: string;
}

// Stable inbound-edge fingerprint per target node: sorted set of
// (source, sourceHandle, targetHandle) so edge id / reorder churn
// doesn't show up as a diff. Mirrors diffGraphs.ts::edgeKey minus the
// ``target`` (we're already partitioning by target).
function inboundKey(e: GraphEdge): string {
  return `${e.source}\0${e.sourceHandle}\0${e.targetHandle}`;
}

function inboundSet(edges: readonly GraphEdge[], target: string): string {
  const keys: string[] = [];
  for (const e of edges) if (e.target === target) keys.push(inboundKey(e));
  keys.sort();
  return keys.join("|");
}

function selfDirtyReasons(
  dn: GraphNode,
  sn: GraphNode | null,
  draftEdges: readonly GraphEdge[],
  snapEdges: readonly GraphEdge[],
): string[] {
  if (sn == null) {
    return ["快照中不存在此节点（新增/未参与上次运行）"];
  }
  const reasons: string[] = [];
  if (
    dn.algorithm_name !== sn.algorithm_name ||
    dn.algorithm_version !== sn.algorithm_version
  ) {
    reasons.push(
      `算法已更改: ${sn.algorithm_name}@${sn.algorithm_version} → ${dn.algorithm_name}@${dn.algorithm_version}`,
    );
  }
  if ((dn.assigned_node_id ?? null) !== (sn.assigned_node_id ?? null)) {
    reasons.push("执行节点已更改");
  }
  if (Boolean(dn.arrayed_toggle) !== Boolean(sn.arrayed_toggle)) {
    reasons.push("arrayed 并行开关已改");
  }
  const dPar = Math.max(1, Number(dn.parallelism ?? 1));
  const sPar = Math.max(1, Number(sn.parallelism ?? 1));
  if (dPar !== sPar) reasons.push(`并行度: ${sPar} → ${dPar}`);

  const dp = dn.params ?? {};
  const sp = sn.params ?? {};
  const keys = new Set([...Object.keys(dp), ...Object.keys(sp)]);
  const changedParams: string[] = [];
  for (const k of keys) {
    if (JSON.stringify(dp[k]) !== JSON.stringify(sp[k])) changedParams.push(k);
  }
  if (changedParams.length > 0) {
    changedParams.sort();
    reasons.push(`参数已改: ${changedParams.join(", ")}`);
  }

  if (inboundSet(draftEdges, dn.id) !== inboundSet(snapEdges, sn.id)) {
    reasons.push("输入连线已改");
  }
  return reasons;
}

// Predecessor map keyed by node id. Empty list for source-only nodes.
function buildPreds(nodes: readonly GraphNode[], edges: readonly GraphEdge[]): Map<string, string[]> {
  const preds = new Map<string, string[]>();
  for (const n of nodes) preds.set(n.id, []);
  for (const e of edges) {
    const bucket = preds.get(e.target);
    if (bucket) bucket.push(e.source);
  }
  return preds;
}

// Kahn topological order over the draft graph. Nodes belonging to a
// cycle (shouldn't happen — canvas validates) are appended at the end
// so the caller still visits them once and can't loop forever.
export function topoOrder(nodes: readonly GraphNode[], edges: readonly GraphEdge[]): string[] {
  const indeg = new Map<string, number>();
  const succ = new Map<string, string[]>();
  for (const n of nodes) {
    indeg.set(n.id, 0);
    succ.set(n.id, []);
  }
  for (const e of edges) {
    indeg.set(e.target, (indeg.get(e.target) ?? 0) + 1);
    succ.get(e.source)?.push(e.target);
  }
  const q: string[] = [];
  for (const [id, d] of indeg) if (d === 0) q.push(id);
  const order: string[] = [];
  while (q.length > 0) {
    const id = q.shift()!;
    order.push(id);
    for (const t of succ.get(id) ?? []) {
      const nd = (indeg.get(t) ?? 0) - 1;
      indeg.set(t, nd);
      if (nd === 0) q.push(t);
    }
  }
  const seen = new Set(order);
  for (const n of nodes) if (!seen.has(n.id)) order.push(n.id);
  return order;
}

export function computeStaleness(
  draft: WorkflowGraph,
  snap: WorkflowGraph | null,
  runtimes: Readonly<Record<string, NodeRuntime | undefined>>,
): Record<string, NodeStaleness | null> {
  const out: Record<string, NodeStaleness | null> = {};
  const snapById = new Map<string, GraphNode>(
    (snap?.nodes ?? []).map((n) => [n.id, n]),
  );
  const snapEdges = snap?.edges ?? [];
  const preds = buildPreds(draft.nodes, draft.edges);
  const order = topoOrder(draft.nodes, draft.edges);
  const draftById = new Map<string, GraphNode>(draft.nodes.map((n) => [n.id, n]));

  for (const id of order) {
    const dn = draftById.get(id);
    if (!dn) continue;
    const rt = runtimes[id];
    if (rt && IN_FLIGHT.has(rt.state)) {
      out[id] = null;
      continue;
    }
    // No baseline snapshot yet OR node absent from snapshot ⇒ self_dirty
    // with the "no snapshot record" reason unless it already ran to done
    // in the current session (rare but possible: dispatched then baseline
    // reset). We still gate on runtime.state === "done" to decide "does
    // this node have any fresh output right now".
    const sn = snap ? (snapById.get(id) ?? null) : null;
    const selfReasons = selfDirtyReasons(dn, sn, draft.edges, snapEdges);
    const hasDoneOutput = rt?.state === "done";
    if (!hasDoneOutput && selfReasons.length === 0) {
      // Config matches the snapshot but the output isn't fresh — the
      // slot needs a (re)run. State vocabulary matches gateway/models.py.
      const prior = rt?.state;
      if (prior === "failed") selfReasons.push("上次运行失败，需要重跑");
      else if (prior === "cancelled") selfReasons.push("上次运行被取消，需要重跑");
      else if (prior === "orphaned") selfReasons.push("上次运行未完成（节点掉线），需要重跑");
      else selfReasons.push("尚未运行");
    }
    if (selfReasons.length > 0) {
      out[id] = { kind: "self_dirty", title: selfReasons.join(" · ") };
      continue;
    }
    // Config matches snapshot AND we have a done output. Check if any
    // ancestor is stale — Kahn order guarantees ``out[p]`` is filled
    // before we get here for every predecessor p.
    let upstreamStale = false;
    for (const p of preds.get(id) ?? []) {
      if (out[p] != null) {
        upstreamStale = true;
        break;
      }
    }
    out[id] = upstreamStale ? { kind: "upstream_dirty", title: "上游输入已变化，本节点结果基于旧输入" } : null;
  }
  return out;
}

// Value-level equality — the App-level runtime-sync effect uses this to
// decide whether to mint a new node identity for xyflow. Skipping the
// check when the memoised staleness object is a fresh reference each
// render but value-equal to before would reset handleBounds on all
// unchanged nodes (see CanvasContext.ts for the failure mode).
export function stalenessEqual(a: NodeStaleness | null | undefined, b: NodeStaleness | null | undefined): boolean {
  if (a === b) return true;
  if (a == null || b == null) return a == null && b == null;
  return a.kind === b.kind && a.title === b.title;
}

// Earliest self_dirty node in topological order — the "start rerunning
// here" jump target for the toolbar. A self_dirty node with no
// self_dirty ancestor IS topologically-earliest by definition, so we
// walk the topo order and return the first self_dirty hit. Upstream
// dirty is skipped: by definition its dirtiness resolves once its
// ancestor is rerun, so it's not a good target for the initial jump.
export function earliestDirtyId(
  draft: WorkflowGraph,
  staleness: Readonly<Record<string, NodeStaleness | null>>,
): string | null {
  const order = topoOrder(draft.nodes, draft.edges);
  for (const id of order) {
    if (staleness[id]?.kind === "self_dirty") return id;
  }
  return null;
}

// Count for the toolbar chip — self_dirty + upstream_dirty combined so
// the operator sees the full "how much of the graph is behind the
// current edits" number in one glance.
export function staleCount(staleness: Readonly<Record<string, NodeStaleness | null>>): number {
  let n = 0;
  for (const id in staleness) if (staleness[id] != null) n += 1;
  return n;
}
