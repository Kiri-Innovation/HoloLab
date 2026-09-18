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
// already use: a tag list plus the effective ``arrayed`` flag. Callers
// format both into a chip string via ``formatTypeLabel``.

import type { CatalogPack, GraphEdge, GraphNode } from "../wire";
import { effectivePortArrayed } from "../tags";

export interface EdgeType {
  tags: string[];
  arrayed: boolean;
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
  if (visited.has(key)) return { tags: ["any"], arrayed: false };
  visited.add(key);

  const node = ctx.nodes.find((n) => n.id === nodeId);
  if (!node) return { tags: [], arrayed: false };
  const pack = ctx.catalogByKey.get(
    `${node.algorithm_name}@${node.algorithm_version}`,
  );
  if (!pack) return { tags: [], arrayed: false };

  const port = pack.outputs[portName];
  if (!port) return { tags: [], arrayed: false };

  const nodeArrayed = Boolean(node.arrayed_toggle);
  const arrayed = effectivePortArrayed(
    port.arrayed,
    pack.arrayable,
    nodeArrayed,
  );

  if (!port.tags_from) {
    return { tags: [...port.tags], arrayed };
  }

  // Follow the wire back — find the edge feeding the referenced input
  // port and recurse on its source. Missing wire → fall back to the
  // declared tags rather than empty (the port still has a nominal type).
  const upstreamInput = port.tags_from;
  const feeder = ctx.edges.find(
    (e) => e.target === nodeId && e.targetHandle === upstreamInput,
  );
  if (!feeder) return { tags: [...port.tags], arrayed };

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
  return { tags: upstream.tags, arrayed };
}

/** Compact label suitable for an edge chip. Long tag lists collapse to
 *  the first tag + "…" so the chip doesn't stretch across the canvas. */
export function formatTypeLabel(t: EdgeType): string {
  if (t.tags.length === 0) return "?";
  const inner = t.tags.length <= 1 ? t.tags[0] : `${t.tags[0]}+${t.tags.length - 1}`;
  return t.arrayed ? `arrayed<${inner}>` : inner;
}

/** Full human-readable form (with the full tag list) — used for the
 *  hover title and for the inspector's larger heading where truncation
 *  isn't a concern. */
export function formatTypeLabelLong(t: EdgeType): string {
  if (t.tags.length === 0) return "unknown";
  const inner = t.tags.join(",");
  return t.arrayed ? `arrayed<${inner}>` : inner;
}
