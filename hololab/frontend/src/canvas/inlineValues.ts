import type { InputPortSpec, GraphNode, GraphEdge } from '../wire';
export function supportsInline(spec: InputPortSpec): boolean {
  return (
    spec.inline !== false &&
    (spec.arrayed || !!spec.dim_labels?.length || spec.storage === 'file') &&
    spec.tags.length === 1 &&
    ['float', 'vec3'].includes(spec.tags[0]) &&
    !spec.content_dims_from &&
    (spec.dim_labels?.length ?? 0) <= 1
  );
}
export function inlineError(
  spec: InputPortSpec,
  value: unknown,
  fanout = false,
): string | null {
  if (value == null) return null;
  if (!supportsInline(spec)) return '此端口不支持内联值';
  const depth =
    (spec.arrayed || spec.dim_labels?.length ? 1 : 0) +
    (fanout && !spec.scalar ? 1 : 0);
  const number = (v: unknown) => typeof v === 'number' && Number.isFinite(v);
  const valid = (v: unknown, d: number): boolean =>
    d
      ? Array.isArray(v) && v.every((x) => valid(x, d - 1))
      : spec.tags[0] === 'float'
        ? number(v)
        : Array.isArray(v) && v.length === 3 && v.every(number);
  return valid(value, depth)
    ? null
    : '值格式无效：需要有限数；vec3 必须含三个数，数组维度须匹配端口';
}
export function inputValue(
  spec: InputPortSpec,
  values: Record<string, unknown>,
  name: string,
): unknown {
  return values[name] ?? spec.inline_default;
}
export function inputValueProblems(
  inputs: Record<string, InputPortSpec>,
  values: Record<string, unknown>,
  connected: (string | null | undefined)[],
  fanout = false,
): string[] {
  return Object.entries(inputs)
    .filter(
      ([name, spec]) =>
        !connected.includes(name) &&
        (inputValue(spec, values, name) == null
          ? spec.required
          : !!inlineError(spec, inputValue(spec, values, name), fanout)),
    )
    .map(([name]) => name);
}
export function inlineIdentity(
  node: GraphNode,
  edges: readonly GraphEdge[],
  defaults: Record<string, unknown> = {},
): string {
  const wired = new Set(
    edges.filter((e) => e.target === node.id).map((e) => e.targetHandle),
  );
  const values = {
    ...defaults,
    ...Object.fromEntries(
      Object.entries(node.port_values ?? {}).filter(([, v]) => v != null),
    ),
  };
  return JSON.stringify(
    Object.keys(values)
      .sort()
      .filter((k) => !wired.has(k) && values[k] != null)
      .map((k) => [k, values[k]]),
  );
}

export function inlineDefaults(pack?: {
  inputs: Record<string, InputPortSpec>;
}): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(pack?.inputs ?? {})
      .filter(([, p]) => p.inline_default != null)
      .map(([k, p]) => [k, p.inline_default]),
  );
}

/** Fresh draft values only. Never apply this to a frozen snapshot. */
export function freshInlineValue(spec: InputPortSpec, fanout = false): unknown {
  if (!supportsInline(spec)) return undefined;
  if (spec.inline_default != null) return structuredClone(spec.inline_default);
  if (spec.arrayed || spec.dim_labels?.length || (fanout && !spec.scalar)) return [];
  return spec.tags[0] === 'vec3' ? [0, 0, 0] : 0;
}
export function reconcileInlineValues(
  inputs: Record<string, InputPortSpec>, values: Record<string, unknown> = {},
  connected: (string | null | undefined)[], fanout = false,
): Record<string, unknown> {
  let next = values;
  for (const name of connected) {
    if (name && Object.prototype.hasOwnProperty.call(next, name)) {
      if (next === values) next = {...values};
      delete next[name];
    }
  }
  for (const [name,spec] of Object.entries(inputs)) {
    if (connected.includes(name) || next[name] != null) continue;
    const value = freshInlineValue(spec, fanout);
    if (value === undefined) continue;
    if (next === values) next = {...values};
    next[name] = value;
  }
  return next;
}
