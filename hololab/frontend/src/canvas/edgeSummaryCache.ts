// Per-handle summary cache used by TypedEdge.
//
// TypedEdge fires ``GET /api/handles/{id}/summary`` as soon as its
// source handle exists — hover is a pure CSS change, not a request
// trigger. Bounded + deduped by ``api.getHandleSummary`` (pLimit(8) +
// session cache) so N edges mounting together fan out at most 8
// in-flight requests and a re-mount pays zero HTTP cost. Successful
// summaries are pinned for the session; failures resolve to ``{}``
// silently so the chip degrades to ``[?]`` without retry-storming.
//
// Only validated runtime_shape evidence supplies measurements. Older responses
// remain renderable, with unknown measured values.

import { parseRuntimeObservation } from "../types/runtimeObservation";
import type { RuntimeShape } from "../types/portTypeIR";
import { getHandleSummary } from "../api";
import { observedValue } from "../types/renderPortType";
import type { LatestOutputHandle } from "../wire";

export interface EdgeSummaryFacts {
  runtimeShape?: RuntimeShape;
  tags?: string[];
  dimLabels?: string[];
}

/** New evidence only. Old response fields stay on the API, not as UI fallback.
 * Both inline graph facts and standalone summaries take this same path. */
export function factsFromSummary(s: { handle_id: string; runtime_shape?: unknown }): EdgeSummaryFacts {
  const runtimeShape = parseRuntimeObservation(s.runtime_shape);
  if (!runtimeShape || runtimeShape.handleId !== s.handle_id) return {};
  const tags = observedValue(runtimeShape.tags);
  const axes = observedValue(runtimeShape.axes);
  return { runtimeShape, ...(tags ? { tags: [...tags] } : {}),
    ...(axes ? { dimLabels: axes.map(axis => axis.label ?? '') } : {}) };
}

// Two parallel maps so ``peek`` can answer without observing an in-
// flight promise: pending → ``pending``, resolved → ``resolved``. The
// resolved value survives for the session (facts are cheap and rarely
// invalidated — a handle's element count doesn't change after emit).
const pending = new Map<string, Promise<EdgeSummaryFacts>>();
const resolved = new Map<string, EdgeSummaryFacts>();

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

/** Seed the cache from the backend's ``latest_run.output_handles``
 *  payload — the compact chip-facts view that ships inline with every
 *  graph GET (see ``latest_runs_for_workflow`` in gateway/workflows.py).
 *
 *  This closes the cold-load flicker where every mounted edge fired
 *  ``getHandleSummary`` on its handle_id before the chip could render
 *  counts; with a seeded cache the chip paints ``image[cam:21]``
 *  synchronously on first render. Only seeds when the entry isn't
 *  already resolved (we prefer a live fetch's fresher probe over the
 *  backend's cached probe, though in practice they should agree).
 */
export function seedEdgeSummaryFacts(
  handleId: string,
  latest: LatestOutputHandle & { runtime_shape?: unknown },
): void {
  if (resolved.has(handleId)) return;
  const facts = latest.handle_id === handleId ? factsFromSummary(latest) : {};
  resolved.set(handleId, facts);
}
