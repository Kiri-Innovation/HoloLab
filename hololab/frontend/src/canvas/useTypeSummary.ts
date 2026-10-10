import { useEffect, useState } from "react";
import type { EdgeType } from "./edgeLabels";
import { loadEdgeSummaryFacts, peekEdgeSummaryFacts, type EdgeSummaryFacts } from "./edgeSummaryCache";

/** Shared by port handles and edge chips; stale handle facts never cross a rewire. */
export function useTypeSummary(type: EdgeType, handleId?: string | null): EdgeType & { runtimeAvailable: boolean } {
  const [loaded, setLoaded] = useState<{ id: string; facts: EdgeSummaryFacts }>();
  const facts = handleId ? peekEdgeSummaryFacts(handleId) ?? (loaded?.id === handleId ? loaded.facts : undefined) : undefined;
  useEffect(() => {
    if (!handleId || peekEdgeSummaryFacts(handleId)) return;
    let active = true;
    void loadEdgeSummaryFacts(handleId).then(facts => { if (active) setLoaded({ id: handleId, facts }); });
    return () => { active = false; };
  }, [handleId]);
  return { ...type, ...facts, runtimeAvailable: facts?.tags !== undefined, dimLabels: facts?.dimLabels ?? type.dimLabels, arrayed: facts?.dimLabels ? facts.dimLabels.length > 0 || Boolean(facts.dimSizes?.length) : type.arrayed };
}
