// Per-handle summary cache used by TypedEdge on hover.
//
// We hit ``GET /api/handles/{id}/summary`` only when the mouse actually
// hovers an edge whose source handle exists — no upfront load, no per-
// frame refetch. Successful summaries are pinned for the session so a
// second hover across the same edge is instant.
//
// A single ``element_count`` / ``internal_count`` payload per handle is
// enough for the chip; more expensive drill-downs (per-element inner
// counts) come from the preview drawer's own summary fetch.

import { getHandleSummary } from "../api";
import type { HandleSummary } from "../wire";

export interface EdgeSummaryFacts {
  elementCount?: number;
  internalCount?: number;
  internalCountKind?: string;
  /** Inner element count for the first outer element when the server
   *  enriched ``fields.entries[].children`` (2-D arrayed only). */
  innerElementCount?: number;
}

// Two parallel maps so ``peek`` can answer without observing an in-
// flight promise: pending → ``pending``, resolved → ``resolved``. The
// resolved value survives for the session (facts are cheap and rarely
// invalidated — a handle's element count doesn't change after emit).
const pending = new Map<string, Promise<EdgeSummaryFacts>>();
const resolved = new Map<string, EdgeSummaryFacts>();

function factsFromSummary(s: HandleSummary): EdgeSummaryFacts {
  const facts: EdgeSummaryFacts = {};
  const explicitElementCount = s.element_count;
  if (typeof explicitElementCount === "number") {
    facts.elementCount = explicitElementCount;
  } else if (Array.isArray(s.fields.entries)) {
    // Legacy backend hasn't set ``element_count`` — infer it from the
    // top-level entry list (arrayed handles are dirs whose children are
    // element dirs). Only trust when the payload isn't truncated so we
    // don't chip a false ``[8]`` when the real count is 100.
    if (!s.fields.truncated) {
      const dirs = s.fields.entries.filter((e) => e.is_dir).length;
      if (dirs > 0) facts.elementCount = dirs;
    }
  }
  if (typeof s.internal_count === "number") {
    facts.internalCount = s.internal_count;
    if (typeof s.internal_count_kind === "string") {
      facts.internalCountKind = s.internal_count_kind;
    }
  }
  // Second dim: look at the first element's ``entry_count`` — the server
  // enriches one level down, which is exactly the 2-D case's inner dim.
  const firstDir = s.fields.entries?.find((e) => e.is_dir);
  if (firstDir?.entry_count != null) {
    facts.innerElementCount = firstDir.entry_count;
  }
  return facts;
}

/** Fetch summary facts for a handle, deduped by id. Never rejects — a
 *  failure resolves to an empty facts object so the chip's ``[?]``
 *  placeholder stays visible. */
export function loadEdgeSummaryFacts(
  handleId: string,
): Promise<EdgeSummaryFacts> {
  const hit = pending.get(handleId);
  if (hit) return hit;
  const p = getHandleSummary(handleId)
    .then(factsFromSummary)
    .catch((): EdgeSummaryFacts => ({}))
    .then((facts) => {
      resolved.set(handleId, facts);
      return facts;
    });
  pending.set(handleId, p);
  return p;
}

/** Synchronous read for facts already resolved. Returns undefined when
 *  the fetch is still pending or has never been requested. */
export function peekEdgeSummaryFacts(
  handleId: string,
): EdgeSummaryFacts | undefined {
  return resolved.get(handleId);
}

/** Test hook — drop all cached state so a scenario can re-observe the
 *  fetch path. Not used by production code. */
export function _resetEdgeSummaryCache(): void {
  pending.clear();
  resolved.clear();
}
