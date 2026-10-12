/** Explicit synthetic observations for unit/browser fixtures, never production inference. */
import type { RuntimeShape } from '../types/portTypeIR';
export function measured(tags: string[], labels: string[] = [], sizes: Array<number | undefined> = [],
  counts: Array<{key: string; value: number}> = [], handleId: string | null = null): RuntimeShape {
  return {handleId, tags:{evidence:{status:'complete',value:tags,source:'test-fixture'}},
    axes:{evidence:{status:'complete',source:'test-fixture',value:Array.from({length:Math.max(labels.length,sizes.length)},(_,i)=>({
      label:labels[i] || null,role:'unknown',size:{evidence:sizes[i] === undefined
        ? {status:'unknown',reason:'not-measured'} : {status:'complete',source:'test-fixture',value:sizes[i]!}}}))}},
    internalCounts:counts.map(c=>({key:c.key,count:{evidence:{status:'complete',source:'test-fixture',value:c.value}}}))};
}
export function measuredPayload(...args: Parameters<typeof measured>) {
  return {schemaVersion:1,...measured(...args)};
}
