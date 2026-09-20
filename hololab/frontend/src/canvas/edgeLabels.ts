// Compute the human-readable "data type" carried by an edge.
//
// Mirrors the backend's ``effective_output_tags`` walk in
// hololab/gateway/workflows.py: generic utility packs (``arrayfy`` /
// ``get-index``) declare an output with ``tags_from: <input_name>`` so the
// element type propagates from whatever the caller wired in. A frontend
// edge label wants the *displayed* type, not the raw declaration, so we
// follow the same chain — a bare walk of the graph's edges + catalog is
// enough because the DAG is small (dozens of nodes) and cycles collapse
// to ``any`` via the visited set.
//
// The result is the same shape ``AlgorithmNode``'s port dot + node badge
// already use: a tag list plus the effective ``arrayed`` flag PLUS the
// dim-label list (outer→inner). Callers format both into a chip string
// via ``formatTypeLabel``.
//
// Chip display rules (see design contract):
//   * ``T``                      — scalar, no counts
//   * ``T(N)``                   — scalar with tag-specific inner count
//   * ``T[N]``                   — 1-D arrayed, unlabeled
//   * ``T[label:N]``             — 1-D arrayed, dim label present
//   * ``T[?]``                   — 1-D arrayed, count unknown (no handle yet)
//   * ``T[F][C]``                — 2-D arrayed, unlabeled (outer first)
//   * ``T[label1:F][label2:C]``  — 2-D arrayed, with dim labels
//   * ``T(N)[label:N]``          — internal count + labeled array size

import type { CatalogPack, GraphEdge, GraphNode } from "../wire";
import { effectivePortArrayed, effectivePortDimLabels } from "../tags";

export interface EdgeType {
  tags: string[];
  arrayed: boolean;
  /** Per-dimension labels, outer-first. Length = arrayed depth. Empty
   *  when scalar. An unlabeled dim is the empty string ``""``. */
  dimLabels: string[];
  /** Runtime counts. Populated by TypedEdge after a handle-summary
   *  fetch; undefined = "unknown". Never populated at layout time. */
  elementCount?: number;
  /** Second-level element count when we've drilled far enough (e.g.
   *  a summary that enriched one level of ``children``). */
  innerElementCount?: number;
  /** Per-dimension sizes from the server's ``dim_sizes`` field, outer
   *  first. Supersedes ``elementCount`` / ``innerElementCount`` when
   *  present. Index 0 maps to ``dimLabels[0]``, etc. */
  dimSizes?: number[];
  /** Tag-specific inner count (cameras.txt row count, etc.). */
  internalCount?: number;
  internalCountKind?: string;
}

/** Resolve a source port's effective type by walking ``tags_from`` when
 *  the pack declares it. Returns ``["any"]`` on cycles / unknown packs so
 *  the label degrades gracefully instead of throwing. */
export function effectiveOutputType(
  nodeId: string,
  portName: string,
  ctx: {
    nodes: readonly GraphNode[];
    edges: readonly GraphEdge[];
    catalogByKey: Map<string, CatalogPack>;
  },
  visited: Set<string> = new Set(),
): EdgeType {
  const key = `${nodeId}::${portName}`;
  if (visited.has(key)) return { tags: ["any"], arrayed: false, dimLabels: [] };
  visited.add(key);

  const node = ctx.nodes.find((n) => n.id === nodeId);
  if (!node) return { tags: [], arrayed: false, dimLabels: [] };
  const pack = ctx.catalogByKey.get(
    `${node.algorithm_name}@${node.algorithm_version}`,
  );
  if (!pack) return { tags: [], arrayed: false, dimLabels: [] };

  const port = pack.outputs[portName];
  if (!port) return { tags: [], arrayed: false, dimLabels: [] };

  const nodeArrayed = Boolean(node.arrayed_toggle);
  const arrayed = effectivePortArrayed(port.arrayed, pack.arrayable, nodeArrayed);
  const dimLabels = effectivePortDimLabels(
    port.arrayed,
    port.dim_labels,
    pack.arrayable,
    nodeArrayed,
  );

  if (!port.tags_from) {
    return { tags: [...port.tags], arrayed, dimLabels };
  }

  // Follow the wire back — find the edge feeding the referenced input
  // port and recurse on its source. Missing wire → fall back to the
  // declared tags rather than empty (the port still has a nominal type).
  const upstreamInput = port.tags_from;
  const feeder = ctx.edges.find(
    (e) => e.target === nodeId && e.targetHandle === upstreamInput,
  );
  if (!feeder) return { tags: [...port.tags], arrayed, dimLabels };

  const upstream = effectiveOutputType(
    feeder.source,
    feeder.sourceHandle,
    ctx,
    visited,
  );
  // Merge: mirror the upstream tags, keep OUR arrayed flag (an arrayfy
  // node's whole point is to change the cardinality relative to its
  // input; get-index does the opposite). Tags themselves come from
  // upstream so ``arrayfy<video-source> → arrayed<video-source>`` reads
  // right on the wire.
  return { tags: upstream.tags, arrayed, dimLabels };
}

/** Base tag string used on the chip (compact form). Empty tag list
 *  degrades to ``"?"``; longer lists collapse to ``first+N`` so the
 *  chip stays bounded. */
function baseChipTag(t: EdgeType): string {
  if (t.tags.length === 0) return "?";
  return t.tags.length <= 1 ? t.tags[0] : `${t.tags[0]}+${t.tags.length - 1}`;
}

/** Compact label suitable for an edge chip. Composes:
 *
 *    <base>                  scalar, no counts
 *    <base>(N)               scalar + internal count
 *    <base>[N]               1-D arrayed, unlabeled
 *    <base>[label:N]         1-D arrayed, dim label present
 *    <base>[F][C]            2-D arrayed, unlabeled
 *    <base>[l1:F][l2:C]      2-D arrayed, with dim labels
 *
 *  Unknown array sizes render as ``[?]`` so the shape stays visible even
 *  when the handle hasn't materialised yet. */
export function formatTypeLabel(t: EdgeType): string {
  const base = baseChipTag(t);
  let s = base;
  if (t.internalCount != null) {
    s += `(${t.internalCount})`;
  }
  if (t.dimLabels.length > 0 || t.arrayed) {
    const depth = Math.max(t.dimLabels.length, t.arrayed ? 1 : 0);
    for (let i = 0; i < depth; i++) {
      // dimSizes is the authoritative source; fall back to the legacy
      // scalar fields for handles that predate the dim_sizes payload.
      let n: number | undefined;
      if (t.dimSizes && i < t.dimSizes.length) {
        n = t.dimSizes[i];
      } else if (i === 0) {
        n = t.elementCount;
      } else if (i === 1) {
        n = t.innerElementCount;
      }
      const label = t.dimLabels[i] ?? "";
      const nStr = n != null ? String(n) : "?";
      s += label ? `[${label}:${nStr}]` : `[${nStr}]`;
    }
  }
  return s;
}

/** Full human-readable form (with the full tag list and dim labels) —
 *  used for the hover title / tooltip. Empty dim label renders as
 *  ``arrayed<T>`` (old-style) rather than ``arrayed<> of T``. */
export function formatTypeLabelLong(t: EdgeType): string {
  if (t.tags.length === 0) return "unknown";
  const inner = t.tags.join(",");
  const labels = t.dimLabels.filter((l) => l && l.length > 0);
  let s: string;
  if (t.dimLabels.length === 0 && !t.arrayed) {
    s = inner;
  } else if (labels.length === 0) {
    // arrayed but no dim names — legacy form.
    const wraps = Math.max(t.dimLabels.length, t.arrayed ? 1 : 0);
    s = inner;
    for (let i = 0; i < wraps; i++) s = `arrayed<${s}>`;
  } else {
    s = `arrayed<${labels.join(",")}> of ${inner}`;
  }
  if (t.internalCount != null) {
    const kind = t.internalCountKind ?? "count";
    s += ` · ${t.internalCount} ${kind}`;
  }
  if (t.dimSizes && t.dimSizes.length > 0) {
    const parts = t.dimSizes.map((n, i) => {
      const lbl = t.dimLabels[i] ?? "";
      return lbl ? `${n} ${lbl}` : String(n);
    });
    s += ` · ${parts.join(", ")}`;
  } else {
    if (t.elementCount != null) {
      s += ` · ${t.elementCount} outer`;
    }
    if (t.innerElementCount != null) {
      s += `, ${t.innerElementCount} inner`;
    }
  }
  return s;
}
