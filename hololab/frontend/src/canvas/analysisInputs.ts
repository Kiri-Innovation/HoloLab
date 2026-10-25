import type { CatalogPack, ComputeNode, SnapshotJob, WorkflowGraph } from '../wire';
import { canonicalPackageName, packageKey } from '../packageIdentity';

/** Stable objects, ordered arrays: explicit null/empty values remain distinct. */
export function stable(value: unknown): string {
  return JSON.stringify(value, (_key, v) => v && typeof v === 'object' && !Array.isArray(v)
    ? Object.fromEntries(Object.keys(v).sort().map(k => [k, v[k]])) : v);
}
export function semanticGraph(graph: WorkflowGraph | null) {
  if (!graph) return null;
  const nodes = graph.nodes.filter(n => n.kind !== 'view');
  const ids = new Set(nodes.map(n => n.id));
  return {
    // Pins participate in Gateway preparation; do not resolve defaults here.
    variables: graph.variables, variable_values: graph.variable_values,
    pinned_params: graph.pinned_params,
    nodes: nodes.map(n => ({id:n.id, kind:n.kind, operation:n.operation, assembly:n.assembly,
      algorithm_name:canonicalPackageName(n.algorithm_name ?? ''), algorithm_version:n.algorithm_version,
      param_bindings:n.param_bindings, params:n.params, port_values:n.port_values, assigned_node_id:n.assigned_node_id,
      arrayed_toggle:n.arrayed_toggle, parallelism:n.parallelism, batch_size:n.batch_size,
    })).sort((a,b) => a.id.localeCompare(b.id)),
    edges: graph.edges.filter(e => ids.has(e.source) && ids.has(e.target)).map(e => ({
      source:e.source, sourceHandle:e.sourceHandle, target:e.target, targetHandle:e.targetHandle,
    })).sort((a,b) => stable(a).localeCompare(stable(b))),
  };
}
export const jobStateGroup = (state: string) => ['pending','assigned','running','orphaned'].includes(state)
  ? 'inflight' : state === 'done' ? 'success' : 'unsuccessful';
export function mainJobsSignature(jobs: SnapshotJob[]) {
  const latest = new Map<string, SnapshotJob>();
  // Match Gateway selection. Timestamps select the record but aren't signature fields.
  for (const j of jobs.filter(j => !j.parent_job_id && j.graph_node_id).sort((a,b) => a.created_ts-b.created_ts)) {
    if (!j.parent_job_id && j.graph_node_id) latest.set(j.graph_node_id,j);
  }
  return [...latest].sort(([a],[b]) => a.localeCompare(b)).map(([id,j]) => ({
    id, job_id:j.job_id, state:jobStateGroup(j.state),
    algorithm_name:canonicalPackageName(j.algorithm_name), algorithm_version:j.algorithm_version,
    params:j.analysis_params_pending ? {unrecorded:true} : {frozen:j.params},
  }));
}
export interface AnalysisContext {
  snapshotId: string | null; snapshotGraph: WorkflowGraph | null;
  jobs: SnapshotJob[]; catalog: CatalogPack[]; devices: ComputeNode[];
  reconnect: number;
}
export function analysisSignatures(graph: WorkflowGraph, context?: AnalysisContext | null) {
  const recipe = semanticGraph(graph);
  const keys = new Set(recipe!.nodes.map(n => packageKey(n.algorithm_name,n.algorithm_version ?? '')));
  const selected = new Set(recipe!.nodes.map(n => n.assigned_node_id).filter(Boolean));
  return {
    edit: stable([recipe, context?.catalog.filter(p => keys.has(packageKey(p.name,p.version)))
      .map(p => ({...p,name:canonicalPackageName(p.name),node_ids:[...p.node_ids].sort()}))
      .sort((a,b) => stable(a).localeCompare(stable(b))),
      context?.devices.filter(d => selected.has(d.node_id)).map(d => ({
        id:d.node_id, connected:d.connected_ts, workspace:d.workspace_root,
        packs:d.packs.filter(p => keys.has(packageKey(p.name,p.version))).sort((a,b) => stable(a).localeCompare(stable(b))), intrinsics:d.intrinsics,
      })).sort((a,b) => a.id.localeCompare(b.id))]),
    baseline: stable([context?.snapshotId, semanticGraph(context?.snapshotGraph ?? null),
      mainJobsSignature(context?.jobs ?? []), context?.reconnect]),
  };
}
