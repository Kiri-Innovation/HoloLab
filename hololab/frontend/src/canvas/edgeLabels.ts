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
//   * ``T``                          — scalar, no counts
//   * ``T(N)``                       — scalar with tag-specific inner count
//   * ``T[N]``                       — 1-D arrayed, unlabeled
//   * ``T[label:N]``                 — 1-D arrayed, dim label present
//   * ``T[?]``                       — 1-D arrayed, count unknown (no handle yet)
//   * ``T[C][F]``                    — 2-D arrayed, unlabeled (inner→outer)
//   * ``T[inner_label:C][outer_label:F]`` — 2-D labeled (inner→outer)
//   * ``T(N)[label:N]``              — internal count + labeled array size
//
// Dimension order: backend data is outer-first (dimLabels[0] = outermost).
// The chip renders inner-first (Python-convention: T[cam:21][frame:100])
// so that the "element type" bracket sits closest to the tag name.

import { runtimeFromLegacy, typeFromLegacy } from "../types/legacyPortType";
import { renderPortType } from "../types/renderPortType";
import type { CatalogPack, GraphEdge, GraphNode } from "../wire";
import { effectivePortArrayed, effectivePortDimLabels } from "../tags";
import { isAlgorithmGraphNode } from "./nodeMeta";

export interface EdgeType {
  tags: string[];
  arrayed: boolean;
  /** Per-dimension labels, outer-first (mirrors backend storage order).
   *  Length = arrayed depth. An unlabeled dim is the empty string ``""``.
   *  Empty when scalar.  formatTypeLabel renders these inner→outer. */
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
  /** Multi-value labeled counts — supersedes internalCount when present.
   *  Items with value=0 are suppressed by the chip formatter. */
  internalCountItems?: Array<{ label: string; value: number }>;
}

// Walk ``tags_from`` back to the ultimate producer. Independent from
// the dim-labels walk (own visited set) — mirrors the backend's
// ``effective_output_tags`` in workflows.py. Merging the two walks into
// one visited set (as the earlier combined resolver did) polluted the
// tag walk with hops the dim walk had already taken, so a ``get-index``
// whose ``tags_from`` and ``dim_labels_from_input`` both point at
// ``arr`` fell through the cycle guard and rendered ``any`` even though
// the upstream was a concrete ``image``.
function walkTags(
  nodeId: string,
  portName: string,
  ctx: EdgeTypeCtx,
  visited: Set<string>,
): string[] {
  const key = `${nodeId}::${portName}`;
  if (visited.has(key)) return ["any"];
  visited.add(key);
  const node = ctx.nodes.find((n) => n.id === nodeId);
  if (!node || !isAlgorithmGraphNode(node)) return [];
  const pack = ctx.catalogByKey.get(
    `${node.algorithm_name}@${node.algorithm_version}`,
  );
  if (!pack) return [];
  const port = pack.outputs[portName];
  if (!port) return [];
  if (!port.tags_from) return [...port.tags];
  const feeder = ctx.edges.find(
    (e) => e.target === nodeId && e.targetHandle === port.tags_from,
  );
  if (!feeder) return [...port.tags];
  return walkTags(feeder.source, feeder.sourceHandle, ctx, visited);
}

// Walk ``dim_labels_from_input`` back to the ultimate producer. Own
// visited set — see the header on walkTags.
function walkDimLabels(
  nodeId: string,
  portName: string,
  ctx: EdgeTypeCtx,
  visited: Set<string>,
): string[] | null {
  const key = `${nodeId}::${portName}`;
  if (visited.has(key)) return null;
  visited.add(key);
  const node = ctx.nodes.find((n) => n.id === nodeId);
  if (!node || !isAlgorithmGraphNode(node)) return null;
  const pack = ctx.catalogByKey.get(
    `${node.algorithm_name}@${node.algorithm_version}`,
  );
  if (!pack) return null;
  const port = pack.outputs[portName];
  if (!port) return null;

  if (port.dim_labels_from) {
    const pv = node.params[port.dim_labels_from];
    if (Array.isArray(pv) && pv.every((v) => typeof v === "string")) {
      return pv as string[];
    }
    return port.dim_labels ? [...port.dim_labels] : [];
  }

  if (port.dim_labels_from_input) {
    const feeder = ctx.edges.find(
      (e) => e.target === nodeId && e.targetHandle === port.dim_labels_from_input,
    );
    if (!feeder) return null;
    const upstream = walkDimLabels(feeder.source, feeder.sourceHandle, ctx, visited);
    if (upstream == null) return null;
    const drop = Math.max(0, port.dim_labels_drop_outer ?? 0);
    return drop >= upstream.length ? [] : upstream.slice(drop);
  }

  return port.dim_labels ? [...port.dim_labels] : [];
}

export interface EdgeTypeCtx {
  nodes: readonly GraphNode[];
  edges: readonly GraphEdge[];
  catalogByKey: Map<string, CatalogPack>;
}

/** Resolve a source port's effective type. Prefers the backend's
 *  ``resolved_outputs`` (populated on every graph GET — see
 *  ``agent_graph_dict`` in gateway/workflows.py) so the frontend and
 *  agents agree on the effective type without duplicating the resolver.
 *  Falls back to a local walk for freshly-added palette drops that
 *  haven't been round-tripped through autosave yet. */
export function effectiveOutputType(
  nodeId: string,
  portName: string,
  ctx: EdgeTypeCtx,
): EdgeType {
  const node = ctx.nodes.find((n) => n.id === nodeId);
  if (!node || !isAlgorithmGraphNode(node)) return { tags: [], arrayed: false, dimLabels: [] };
  const pack = ctx.catalogByKey.get(
    `${node.algorithm_name}@${node.algorithm_version}`,
  );
  if (!pack) return { tags: [], arrayed: false, dimLabels: [] };
  const port = pack.outputs[portName];
  if (!port) return { tags: [], arrayed: false, dimLabels: [] };

  // Fast path: backend already resolved this port. Trust it.
  const resolved = node.resolved_outputs?.[portName];
  if (resolved) {
    return {
      tags: [...resolved.tags],
      arrayed: resolved.arrayed,
      dimLabels: [...resolved.dim_labels],
    };
  }

  // Local mirror for pre-autosave state. Two independent walks so a
  // port whose tags_from and dim_labels_from_input reference the same
  // upstream input doesn't self-poison its own cycle guard.
  const nodeArrayed = Boolean(node.arrayed_toggle);
  const tags = port.tags_from
    ? walkTags(nodeId, portName, ctx, new Set())
    : [...port.tags];

  let dimLabels: string[];
  let arrayed = effectivePortArrayed(port.arrayed, pack.arrayable, nodeArrayed);
  if (port.dim_labels_from_input) {
    const walked = walkDimLabels(nodeId, portName, ctx, new Set());
    if (walked != null) {
      dimLabels = walked;
      // Arrayed cardinality mirrors the derived shape: a drop that
      // collapses the outermost layer must also flip the port to scalar,
      // else the chip would render an unlabeled ``[?]`` bracket for a
      // value that is genuinely a single element.
      arrayed = walked.length > 0;
    } else {
      dimLabels = [];
      arrayed = false;
    }
  } else if (port.dim_labels_from) {
    const pv = node.params[port.dim_labels_from];
    dimLabels = Array.isArray(pv) && pv.every((v) => typeof v === "string")
      ? (pv as string[])
      : effectivePortDimLabels(
          port.arrayed,
          port.dim_labels,
          pack.arrayable,
          nodeArrayed,
        );
  } else {
    dimLabels = effectivePortDimLabels(
      port.arrayed,
      port.dim_labels,
      pack.arrayable,
      nodeArrayed,
    );
  }

  return { tags, arrayed, dimLabels };
}

/** Compatibility facades for existing edge/inspector/snapshot consumers.
 * All display formatting is delegated to the IR renderer. */
export function formatTypeLabel(t: EdgeType): string {
  return renderPortType(typeFromLegacy(t), { mode: 'compact', runtime: runtimeFromLegacy({ ...t, runtimeAvailable: undefined }) });
}

export function formatTypeLabelLong(t: EdgeType): string {
  return renderPortType(typeFromLegacy(t), { mode: 'long', runtime: runtimeFromLegacy(t) });
}
