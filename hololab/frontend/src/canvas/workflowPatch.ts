import type { WorkflowGraph } from "../wire";

export type DocumentState = { name: string; graph: WorkflowGraph };
export type WorkflowOp = Record<string, any> & { op: string };
const equal = (a: unknown, b: unknown) =>
  JSON.stringify(a) === JSON.stringify(b);
const nodeFields = [
  "kind",
  "operation",
  "assembly",
  "algorithm_name",
  "algorithm_version",
  "code_identity",
  "position",
  "params",
  "param_bindings",
  "local_params",
  "port_values",
  "device",
  "assigned_node_id",
  "arrayed_toggle",
  "parallelism",
  "preview_open",
  "view_type",
  "title",
];
const graphFields = [
  "recipe_dependencies",
  "variables",
  "variable_values",
  "execution",
  "pinned_params",
];
const defaults: Record<string, unknown> = {
  kind: "algorithm",
  device: "default",
  position: { x: 0, y: 0 },
  params: {},
  port_values: {},
  param_bindings: {},
  local_params: [],
  arrayed_toggle: false,
  parallelism: 1,
};
export function diffDocument(
  before: DocumentState,
  after: DocumentState,
): WorkflowOp[] {
  const ops: WorkflowOp[] = [];
  if (before.name !== after.name)
    ops.push({ op: "name.set", value: after.name });
  const old = new Map(before.graph.nodes.map((n) => [n.id, n]));
  const next = new Map(after.graph.nodes.map((n) => [n.id, n]));
  for (const id of old.keys())
    if (!next.has(id)) ops.push({ op: "node.remove", id });
  for (const node of after.graph.nodes) {
    const previous = old.get(node.id);
    if (!previous) {
      ops.push({ op: "node.add", node });
      continue;
    }
    for (const field of nodeFields) {
      if (field === "code_identity" && !(field in node)) continue;
      const a = (previous as any)[field] ?? defaults[field] ?? null,
        b = (node as any)[field] ?? defaults[field] ?? null;
      if (equal(a, b)) continue;
      if (["params", "port_values", "param_bindings"].includes(field)) {
        for (const key of new Set([
          ...Object.keys(a ?? {}),
          ...Object.keys(b ?? {}),
        ])) {
          if (!equal(a?.[key], b?.[key]))
            ops.push({
              op: "node.map.set",
              id: node.id,
              field,
              key,
              value: b?.[key],
              remove: !(key in (b ?? {})),
            });
        }
      } else if (field === "position")
        ops.push({
          op: "node.pos.set",
          id: node.id,
          position: b ?? { x: 0, y: 0 },
        });
      else
        ops.push({
          op: "node.set",
          id: node.id,
          value: { [field]: b ?? null },
        });
    }
  }
  const edges = new Map(before.graph.edges.map((e) => [e.id, e]));
  const nextEdges = new Map(after.graph.edges.map((e) => [e.id, e]));
  for (const id of edges.keys())
    if (!nextEdges.has(id)) ops.push({ op: "edge.remove", id });
  for (const edge of after.graph.edges) {
    const previous = edges.get(edge.id);
    if (!previous) ops.push({ op: "edge.add", edge });
    else if (!equal(previous, edge))
      ops.push({ op: "edge.set", id: edge.id, value: edge });
  }
  for (const field of graphFields) {
    const fallback =
      field === "execution" ? null : field === "recipe_dependencies" ? [] : {};
    const a = (before.graph as any)[field] ?? fallback,
      b = (after.graph as any)[field] ?? fallback;
    if (equal(a, b)) continue;
    if (["variables", "variable_values", "pinned_params"].includes(field)) {
      for (const key of new Set([...Object.keys(a), ...Object.keys(b)]))
        if (!equal(a[key], b[key]))
          ops.push({
            op: "graph.map.set",
            field,
            key,
            value: b[key],
            remove: !(key in b),
          });
    } else ops.push({ op: "graph.set", field, value: b });
  }
  return ops;
}

/** Rebase only locally edited elements. Remote changes on other fields survive. */
export function applyOps(
  document: DocumentState,
  ops: WorkflowOp[],
): DocumentState {
  const result = structuredClone(document);
  let nodes = result.graph.nodes as any[],
    edges = result.graph.edges as any[];
  for (const op of ops) {
    const node = nodes.find((n) => n.id === op.id);
    if (op.op === "name.set") result.name = op.value;
    else if (op.op === "node.add") {
      nodes = nodes.filter((n) => n.id !== op.node.id);
      nodes.push(op.node);
    } else if (op.op === "node.remove") {
      nodes = nodes.filter((n) => n.id !== op.id);
      edges = edges.filter((e) => e.source !== op.id && e.target !== op.id);
    } else if (op.op === "node.set" && node) Object.assign(node, op.value);
    else if (op.op === "node.pos.set" && node) node.position = op.position;
    else if (op.op === "node.map.set" && node) {
      node[op.field] ??= {};
      if (op.remove) delete node[op.field][op.key];
      else node[op.field][op.key] = op.value;
    } else if (op.op === "edge.add") {
      if (
        nodes.some((n) => n.id === op.edge.source) &&
        nodes.some((n) => n.id === op.edge.target)
      ) {
        edges = edges.filter((e) => e.id !== op.edge.id);
        edges.push(op.edge);
      }
    } else if (op.op === "edge.remove")
      edges = edges.filter((e) => e.id !== op.id);
    else if (op.op === "edge.set") {
      const edge = edges.find((e) => e.id === op.id);
      if (edge) Object.assign(edge, op.value);
    } else if (op.op === "graph.set")
      (result.graph as any)[op.field] = op.value;
    else if (op.op === "graph.map.set") {
      const graph = result.graph as any;
      graph[op.field] ??= {};
      if (op.remove) delete graph[op.field][op.key];
      else graph[op.field][op.key] = op.value;
    }
  }
  result.graph.nodes = nodes;
  result.graph.edges = edges;
  return result;
}
