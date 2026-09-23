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
// A single ``element_count`` / ``internal_count`` payload per handle is
// enough for the chip; more expensive drill-downs (per-element inner
// counts) come from the preview drawer's own summary fetch.

import { getHandleSummary } from "../api";
import type { HandleSummary, LatestOutputHandle } from "../wire";

export interface EdgeSummaryFacts {
  elementCount?: number;
  internalCount?: number;
  internalCountKind?: string;
  /** Multi-value labeled counts. Supersedes internalCount when present.
   *  Items with value=0 are rendered but hidden by the chip formatter. */
  internalCountItems?: Array<{ label: string; value: number }>;
  /** Inner element count for the first outer element when the server
   *  enriched ``fields.entries[].children`` (2-D arrayed only). */
  innerElementCount?: number;
  /** Per-dimension sizes from ``dim_sizes``, outer first. Supersedes
   *  ``elementCount`` / ``innerElementCount`` when present. */
  dimSizes?: number[];
  /** Runtime-resolved dim labels from ``HandleSummary.dim_labels``.
   *  Overrides the static catalog labels in TypedEdge — critical for
   *  generic ports like ``regroup.out`` where the catalog has ``[]``
   *  and the actual labels come from the job's ``output_dims`` param. */
  dimLabels?: string[];
}

// Two parallel maps so ``peek`` can answer without observing an in-
// flight promise: pending → ``pending``, resolved → ``resolved``. The
// resolved value survives for the session (facts are cheap and rarely
// invalidated — a handle's element count doesn't change after emit).
const pending = new Map<string, Promise<EdgeSummaryFacts>>();
const resolved = new Map<string, EdgeSummaryFacts>();

function factsFromSummary(s: HandleSummary): EdgeSummaryFacts {
  const facts: EdgeSummaryFacts = {};

  // dim_sizes is the authoritative multi-dim count source. Backfill the
  // legacy scalar fields so callers that only inspect elementCount still
  // get the right value for the common 1-D case.
  if (Array.isArray(s.dim_sizes) && s.dim_sizes.length > 0) {
    facts.dimSizes = s.dim_sizes as number[];
    facts.elementCount = s.dim_sizes[0];
    if (s.dim_sizes.length > 1) facts.innerElementCount = s.dim_sizes[1];
  } else {
    // Legacy path: backend predates dim_sizes.
    const explicitElementCount = s.element_count;
    if (typeof explicitElementCount === "number") {
      facts.elementCount = explicitElementCount;
    } else if (Array.isArray(s.fields.entries)) {
      // Older backend hasn't set element_count — infer from top-level
      // dir list. Only trust when the payload isn't truncated.
      if (!s.fields.truncated) {
        const dirs = s.fields.entries.filter((e) => e.is_dir).length;
        if (dirs > 0) facts.elementCount = dirs;
      }
    }
    // Second dim: first element's entry_count from the server drill.
    const firstDir = s.fields.entries?.find((e) => e.is_dir);
    if (firstDir?.entry_count != null) {
      facts.innerElementCount = firstDir.entry_count;
    }
  }

  if (Array.isArray(s.dim_labels) && s.dim_labels.length > 0) {
    facts.dimLabels = s.dim_labels as string[];
  }

  if (Array.isArray(s.internal_count_items) && s.internal_count_items.length > 0) {
    facts.internalCountItems = s.internal_count_items as Array<{ label: string; value: number }>;
  } else if (typeof s.internal_count === "number") {
    facts.internalCount = s.internal_count;
    if (typeof s.internal_count_kind === "string") {
      facts.internalCountKind = s.internal_count_kind;
    }
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
  latest: LatestOutputHandle,
): void {
  if (resolved.has(handleId)) return;
  const facts: EdgeSummaryFacts = {};
  if (latest.dim_sizes && latest.dim_sizes.length > 0) {
    facts.dimSizes = latest.dim_sizes;
    facts.elementCount = latest.dim_sizes[0];
    if (latest.dim_sizes.length > 1) facts.innerElementCount = latest.dim_sizes[1];
  } else if (typeof latest.element_count === "number") {
    facts.elementCount = latest.element_count;
  }
  if (latest.dim_labels && latest.dim_labels.length > 0) {
    facts.dimLabels = latest.dim_labels;
  }
  if (latest.internal_count_items && latest.internal_count_items.length > 0) {
    facts.internalCountItems = latest.internal_count_items;
  } else if (typeof latest.internal_count === "number") {
    facts.internalCount = latest.internal_count;
    if (typeof latest.internal_count_kind === "string") {
      facts.internalCountKind = latest.internal_count_kind;
    }
  }
  resolved.set(handleId, facts);
}
