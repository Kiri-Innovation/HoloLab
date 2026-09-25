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
// Three kinds of staleness are surfaced:
//
//   self_dirty          — this node's own config diverged from the
//                         snapshot, OR the snapshot has no fresh output
//                         for it (never ran, failed, cancelled,
//                         orphaned, or brand-new).
//   upstream_dirty      — this node itself matches the snapshot and has
//                         a ``done`` output, but at least one ancestor
//                         is self_dirty/upstream_dirty. The output is
//                         built on now-outdated inputs.
//   inflight_old_params — a job for this node is still running (or
//                         pending/assigned), but the draft has been
//                         edited since that job was dispatched, so the
//                         command line in flight is using the OLD
//                         params. Rerunning after it lands is required
//                         to apply the current draft. Motivating case
//                         (2026-09-21): operator saw ``use_gpu=1`` in
//                         the config drawer while the running shard was
//                         invoked with ``--use-gpu 0``; the running job
//                         froze params at snapshot time, the draft
//                         changed after, and the UI gave no signal that
//                         the two had diverged.
//
// The snapshot canvas never receives ``staleness`` in its node data
// (SnapshotCanvas builds nodes independently), and AlgorithmNode
// double-guards on ``readOnly``.

import type { CatalogPack, GraphEdge, GraphNode, SnapshotJob, WorkflowGraph } from "../wire";
import type { NodeRuntime } from "./AlgorithmNode";

const IN_FLIGHT = new Set(["pending", "assigned", "running"]);

// Look up the manifest ``params:`` defaults for a pack version. Mirrors
// the backend's ``merged_params_with_defaults`` (gateway/execution.py) so
// the "unchanged by default-fill" invariant is enforced on both sides:
//
//   * At dispatch the gateway fills every manifest default the draft
//     doesn't override into ``Job.params`` (and the snapshot's
//     ``graph.nodes[i].params``), then renders the shard template
//     against the merged dict — the workflow's canonical run params.
//   * The draft on the canvas stays sparse (only keys the operator
//     explicitly set), because autosave preserves what the user typed
//     rather than what the pack would compute.
//
// Comparing the sparse draft directly against the merged snapshot as
// the pre-normalisation code did (``for k in union(draft, snap): if
// dp[k] !== sp[k] flag``) flagged every default-fill key as a change
// on every cold load. Merging defaults into both sides here brings the
// two into the same shape so the diff surfaces only real overrides
// (``iterations: 500 → 50``) and hides the "same-as-default" noise
// (``max_width: undefined → 0`` when the manifest default is ``0``).
export type PackDefaults = (name: string, version: string) => Record<string, unknown>;

// Build a PackDefaults closure over a catalog map. Missing packs return
// an empty defaults dict — the compare then falls back to the raw
// draft-vs-frozen diff for that node (unchanged legacy behaviour), so a
// catalog that hasn't hydrated yet doesn't produce a false-negative
// where a real edit gets hidden. Callers should feed the same
// ``catalogByKey`` the App uses for the palette / hydration so the
// versions here match what a run would actually pull.
export function packDefaultsFromCatalog(
  catalogByKey: ReadonlyMap<string, CatalogPack>,
): PackDefaults {
  return (name, version) => {
    const pack = catalogByKey.get(`${name}@${version}`);
    if (!pack) return {};
    const out: Record<string, unknown> = {};
    for (const [k, spec] of Object.entries(pack.params)) {
      if (spec && "default" in spec) out[k] = spec.default;
    }
    return out;
  };
}

function mergedParams(
  params: Record<string, unknown> | null | undefined,
  defaults: Record<string, unknown>,
): Record<string, unknown> {
  return { ...defaults, ...(params ?? {}) };
}

export interface NodeStaleness {
  kind: "self_dirty" | "upstream_dirty" | "inflight_old_params";
  // Human-readable one-line explanation for the ``title`` tooltip on
  // the badge. For self_dirty this enumerates the actual changes
  // (params, algorithm version, edges, …) so the operator can tell
  // which edit forces a rerun. For inflight_old_params it also names
  // the snapshot id short-hash the in-flight job is running against.
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

// Structural comparison of the draft node against a *frozen* baseline.
// Extracted from selfDirtyReasons so the in-flight-drift check
// (frozen = the SnapshotJob's ``params`` / algorithm at dispatch) can
// reuse the exact same rules as the snapshot-vs-draft check — the two
// must agree on what "materially changed" means, otherwise the amber
// badge and the inflight-old-params chip could disagree and the
// operator would have to guess which one is authoritative.
function paramsAndAlgoReasons(
  dn: GraphNode,
  frozen: {
    algorithm_name: string;
    algorithm_version: string;
    params: Record<string, unknown>;
  },
  packDefaults: PackDefaults,
): string[] {
  const reasons: string[] = [];
  if (
    dn.algorithm_name !== frozen.algorithm_name ||
    dn.algorithm_version !== frozen.algorithm_version
  ) {
    reasons.push(
      `算法已更改: ${frozen.algorithm_name}@${frozen.algorithm_version} → ${dn.algorithm_name}@${dn.algorithm_version}`,
    );
  }
  // Normalise both sides against manifest defaults before diffing so a
  // draft the operator hasn't touched compares equal to the dispatch-
  // merged snapshot params for the same pack version. Each side uses
  // its OWN pack version's defaults — a version diff is already flagged
  // above, and using the wrong side's defaults would hide legitimate
  // param changes newer versions introduced. See ``packDefaultsFromCatalog``
  // for the mirror against gateway/execution.py:merged_params_with_defaults.
  const dp = mergedParams(dn.params, packDefaults(dn.algorithm_name, dn.algorithm_version));
  const sp = mergedParams(frozen.params, packDefaults(frozen.algorithm_name, frozen.algorithm_version));
  const keys = new Set([...Object.keys(dp), ...Object.keys(sp)]);
  const changedParams: string[] = [];
  for (const k of keys) {
    if (JSON.stringify(dp[k]) !== JSON.stringify(sp[k])) changedParams.push(k);
  }
  if (changedParams.length > 0) {
    changedParams.sort();
    reasons.push(`参数已改: ${changedParams.join(", ")}`);
  }
  return reasons;
}

function selfDirtyReasons(
  dn: GraphNode,
  sn: GraphNode | null,
  draftEdges: readonly GraphEdge[],
  snapEdges: readonly GraphEdge[],
  packDefaults: PackDefaults,
): string[] {
  if (sn == null) {
    return ["快照中不存在此节点（新增/未参与上次运行）"];
  }
  const reasons: string[] = paramsAndAlgoReasons(dn, {
    algorithm_name: sn.algorithm_name,
    algorithm_version: sn.algorithm_version,
    params: sn.params ?? {},
  }, packDefaults);
  if ((dn.assigned_node_id ?? null) !== (sn.assigned_node_id ?? null)) {
    reasons.push("执行节点已更改");
  }
  if (Boolean(dn.arrayed_toggle) !== Boolean(sn.arrayed_toggle)) {
    reasons.push("arrayed 并行开关已改");
  }
  const dPar = Math.max(1, Number(dn.parallelism ?? 1));
  const sPar = Math.max(1, Number(sn.parallelism ?? 1));
  if (dPar !== sPar) reasons.push(`并行度: ${sPar} → ${dPar}`);
  const dBatch = Math.max(1, Number(dn.batch_size ?? 1));
  const sBatch = Math.max(1, Number(sn.batch_size ?? 1));
  if (dBatch !== sBatch) reasons.push(`批处理大小: ${sBatch} → ${dBatch}`);

  if (inboundSet(draftEdges, dn.id) !== inboundSet(snapEdges, sn.id)) {
    reasons.push("输入连线已改");
  }
  return reasons;
}

// The oldest in-flight job for one graph node — for a fan-out this is
// the parent coordinator (created before its shards), matching what
// pickRepresentativeJob returns in nodeRuntime.ts. All shards in a
// fan-out share the parent's ``params`` (fan-out doesn't re-parameterise
// per shard), so any one in-flight job is enough to answer "what params
// is the live run using?" — we prefer the parent for stability.
function oldestInFlightJob(
  jobs: readonly SnapshotJob[],
  gnid: string,
): SnapshotJob | null {
  let best: SnapshotJob | null = null;
  for (const j of jobs) {
    if (j.graph_node_id !== gnid) continue;
    if (!IN_FLIGHT.has(j.state)) continue;
    if (best == null || j.created_ts < best.created_ts) best = j;
  }
  return best;
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
  // Live snapshot jobs — used only to answer "for nodes that are still
  // in-flight, do the frozen params on the running job match the draft?"
  // Optional (defaults to []) because the read-only snapshot canvas
  // never asks about drift and older call sites shouldn't need to
  // thread this arg through just to satisfy the signature.
  snapshotJobs: readonly SnapshotJob[] = [],
  // Manifest defaults lookup — mirrors the gateway's dispatch-time
  // ``merged_params_with_defaults`` so a sparse draft (only operator-
  // touched keys) compares equal to the dispatch-merged snapshot
  // params when nothing was actually changed. Default returns an empty
  // dict, which reproduces the pre-normalisation compare — legacy call
  // sites keep working, just with the same false positives they had
  // before. Real call sites should pass ``packDefaultsFromCatalog``.
  packDefaults: PackDefaults = () => ({}),
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
      // In-flight: the status dot already tells the state story, but
      // the operator can't see whether the running job's frozen params
      // match the current draft. Compare against the oldest in-flight
      // job (parent for fan-outs); flag drift if algorithm or params
      // moved since dispatch. Edges/parallelism aren't compared here
      // because a running job's snapshot has already captured its edge
      // topology — the draft-vs-snapshot edge diff is already reported
      // by the sibling amber badge once the job lands.
      const inflightJob = oldestInFlightJob(snapshotJobs, id);
      if (inflightJob != null) {
        // The frozen ``Job.params`` on the dispatched row is ALREADY
        // merged with manifest defaults (gateway/execution.py at Job
        // creation). The draft on the canvas is not. paramsAndAlgoReasons
        // normalises both with ``packDefaults`` so a running job whose
        // draft the operator hasn't touched compares equal to the
        // frozen row and the ``inflight_old_params`` chip doesn't fire
        // spuriously.
        const driftReasons = paramsAndAlgoReasons(dn, {
          algorithm_name: inflightJob.algorithm_name,
          algorithm_version: inflightJob.algorithm_version,
          params: inflightJob.params ?? {},
        }, packDefaults);
        if (driftReasons.length > 0) {
          const jobShort = inflightJob.job_id.slice(0, 7);
          out[id] = {
            kind: "inflight_old_params",
            title: `正在跑的 job 使用旧参数（job ${jobShort}）· ${driftReasons.join(" · ")} · 完成后需重新运行以应用当前草稿`,
          };
          continue;
        }
      }
      out[id] = null;
      continue;
    }
    // No baseline snapshot yet OR node absent from snapshot ⇒ self_dirty
    // with the "no snapshot record" reason unless it already ran to done
    // in the current session (rare but possible: dispatched then baseline
    // reset). We still gate on runtime.state === "done" to decide "does
    // this node have any fresh output right now".
    const sn = snap ? (snapById.get(id) ?? null) : null;
    const selfReasons = selfDirtyReasons(dn, sn, draft.edges, snapEdges, packDefaults);
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
