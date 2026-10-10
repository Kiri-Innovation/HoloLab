import { useEffect, useState } from "react";
import type { EdgeType } from "./edgeLabels";
import { loadEdgeSummaryFacts, peekEdgeSummaryFacts, type EdgeSummaryFacts } from "./edgeSummaryCache";

/** Shared by port handles and edge chips; stale handle facts never cross a rewire. */
export function useTypeSummary(type: EdgeType, handleId?: string | null): EdgeType & { runtimeAvailable: boolean } {
  const [loaded, setLoaded] = useState<{ id: string; facts: EdgeSummaryFacts }>();
  const facts = handleId ? peekEdgeSummaryFacts(handleId) ?? (loaded?.id === handleId ? loaded.facts : undefined) : undefined;
  useEffect(() => {
    if (!handleId) return;
    const cached = peekEdgeSummaryFacts(handleId);
    // Inline latest-run facts can have only an outer count. Fetch the full
    // summary once for array shapes whose authoritative dimensions are absent.
    if (cached && (!type.arrayed || cached.dimSizes?.length)) return;
    let active = true;
    void loadEdgeSummaryFacts(handleId).then(facts => { if (active) setLoaded({ id: handleId, facts }); });
    return () => { active = false; };
  }, [handleId, type.arrayed]);
  return mergeTypeSummary(type, facts);
}

/** Empty dimension names do not cancel a resolved array shape. Internal counts
 * (poses, points, etc.) never supply an outer dimension. */
export function mergeTypeSummary(type: EdgeType, facts?: EdgeSummaryFacts): EdgeType & { runtimeAvailable: boolean } {
  return {
    ...type, ...facts,
    runtimeAvailable: facts?.tags !== undefined,
    dimLabels: facts?.dimLabels?.length ? facts.dimLabels : type.dimLabels,
    arrayed: type.arrayed || Boolean(facts?.dimLabels?.length || facts?.dimSizes?.length),
  };
}
