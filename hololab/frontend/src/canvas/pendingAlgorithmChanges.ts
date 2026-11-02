import { samePackage } from '../packageIdentity';
import type { WorkflowGraph } from '../wire';
import type { ExecutionAnalysis } from './executionAnalysis';
import { isAlgorithmGraphNode } from './nodeMeta';

/** Conservative presentation while analysis is pending (or failed).
 * Package identity needs no backend parameter resolution. Never present evidence
 * for a different algorithm as current; leave effective values to the Gateway.
 */
export function pendingAlgorithmChanges(graph: WorkflowGraph, evidence: ExecutionAnalysis): ExecutionAnalysis {
  const previous = new Map(evidence.graph.nodes.filter(isAlgorithmGraphNode).map(n => [n.id, n]));
  const algorithms = new Set(graph.nodes.filter(isAlgorithmGraphNode).map(n => n.id));
  const reasons = new Map<string, string>();
  for (const node of graph.nodes.filter(isAlgorithmGraphNode)) {
    const old = previous.get(node.id);
    if (old && !samePackage(node.algorithm_name, node.algorithm_version, old.algorithm_name, old.algorithm_version)) {
      reasons.set(node.id, `算法已更改: ${old.algorithm_name}@${old.algorithm_version} → ${node.algorithm_name}@${node.algorithm_version}（检查中）`);
    }
  }
  if (!reasons.size) return evidence;
  const dirty = new Set(reasons.keys());
  const queue = [...dirty];
  for (let i = 0; i < queue.length; i++) {
    for (const edge of graph.edges) {
      if (edge.source === queue[i] && algorithms.has(edge.target) && !dirty.has(edge.target)) {
        dirty.add(edge.target); queue.push(edge.target);
      }
    }
  }
  const inflight = new Set((evidence.canvas_jobs ?? [])
    .filter(j => !j.parent_job_id && ['pending', 'assigned', 'running', 'orphaned'].includes(j.state))
    .map(j => j.graph_node_id));
  const staleness = {...evidence.staleness};
  for (const id of dirty) {
    const reason = reasons.get(id);
    // Preserve a node's own stronger dirty evidence when only its input changed.
    if (!reason && staleness[id]) continue;
    const title = reason ?? '上游算法已更改（检查中）';
    staleness[id] = {kind: inflight.has(id) ? 'inflight_old_params' : reason ? 'self_dirty' : 'upstream_dirty',
      title, changeReasons: [title]};
  }
  return {...evidence, staleness,
    diff: [...evidence.diff, ...[...reasons.values()].map(description => ({description}))]};
}
