import { stalenessEqual, type NodeStaleness } from './staleness';
import { analysisMetrics } from './analysisMetrics';
/** Preserve node AND array identity when Gateway evidence hasn't changed. */
export function applyStaleness<T extends {id:string; data:any}>(nodes:T[], states:Record<string,NodeStaleness|null>):T[] {
  let changed = false;
  const next = nodes.map(n => {
    const value = states[n.id] ?? null;
    if (stalenessEqual(n.data.staleness, value)) return n;
    changed = true; analysisMetrics.replaced++;
    return {...n,data:{...n.data,staleness:value}};
  });
  return changed ? next : nodes;
}
