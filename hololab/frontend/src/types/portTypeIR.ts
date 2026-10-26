/** Versioned, JSON-safe type semantics. Never accepts or parses display text.
 * See docs/type-ir.md. Runtime observations remain separate from contracts. */
import type { PortSpec } from '../wire';

export type Axis = Readonly<{ label: string | null }>;
export type TypeExpr =
  | Readonly<{ kind: 'unknown'; reason: string }>
  | Readonly<{ kind: 'object'; tags: readonly string[]; contentAxes: readonly Axis[] }>
  | Readonly<{ kind: 'array'; axis: Axis; element: TypeExpr }>
  | Readonly<{ kind: 'union'; variants: readonly TypeExpr[] }>;

/** Preserve manifest intent, including derivation directives, separately from
 * resolved views. dim_labels may describe aggregate OR content dimensions. */
export type PortDeclaration = Readonly<Pick<PortSpec, 'tags' | 'arrayed' | 'scalar' | 'dim_labels'> & {
  require_axis_label_match?: boolean;
  tags_from?: string | null;
  dim_labels_from?: string | null;
  dim_labels_from_input?: string | null;
  dim_labels_drop_outer?: number | null;
  content_dims_from?: string | null;
  any_of?: readonly Readonly<{ tags: readonly string[]; arrayed?: boolean; dim_labels?: readonly string[] }>[];
}>;

export type Evidence<T> =
  | Readonly<{ status: 'unknown'; reason: string }>
  | Readonly<{ status: 'sampled'; value: T; source: string; sample: string }>
  | Readonly<{ status: 'complete'; value: T; source: string }>;

/** Complete means measured over this handle/scope, never inferred from a
 * manifest. legacyValue remains accepted in the versioned wire shape, but the
 * UI ignores it: unknown evidence always renders as unmeasured. */
export type Observation<T> = Readonly<{ evidence: Evidence<T>; legacyValue?: T }>;
export type ObservedAxis = Readonly<{
  label: string | null;
  role: 'collection' | 'content' | 'unknown';
  size: Observation<number>;
  /** Complete per-parent bounds when a collection axis is ragged. */
  sizeRange?: Observation<Readonly<{ min: number; max: number }>>;
}>;
export type RuntimeShape = Readonly<{
  handleId: string | null;
  /** Transitional name for a legacy single internal count. */
  legacyCountKind?: string;
  tags: Observation<readonly string[]>;
  /** Ordered outer first; unknown is distinct from a known scalar ([]). */
  axes: Observation<readonly ObservedAxis[]>;
  internalCounts: readonly Readonly<{ key: string; count: Observation<number> }>[];
}>;

export type Participation = 'none' | 'map-input' | 'broadcast-input' | 'collect-output';
export type TypeViews = Readonly<{
  participation: Participation;
  invocation: TypeExpr;
  aggregate: TypeExpr;
}>;
export type PortTypeIR = Readonly<{
  schemaVersion: 1;
  declaration: PortDeclaration;
  views: TypeViews;
  runtime: RuntimeShape;
}>;

export type TypeResult =
  | Readonly<{ ok: true; type: TypeExpr }>
  | Readonly<{ ok: false; reason: 'unknown-type' | 'not-array' | 'invalid-depth'; type: TypeExpr }>;

export function unknownType(reason: string): TypeExpr {
  return { kind: 'unknown', reason };
}

/** `any` is a known wildcard tag, not an unresolved/unknown type. */
export function objectType(tags: readonly string[], contentAxes: readonly Axis[] = []): TypeExpr {
  if (!tags.length) return unknownType('missing-tags');
  return { kind: 'object', tags: [...tags], contentAxes: contentAxes.map(axis => ({ ...axis })) };
}

/** Add exactly one collection layer; labels are names, not type expressions. */
export function wrapArray(element: TypeExpr, axis: Axis = { label: null }): TypeExpr {
  return { kind: 'array', axis: { ...axis }, element };
}

/** Outer-first axes; unknown leaf types retain all known collection layers. */
export function wrapArrays(element: TypeExpr, axes: readonly Axis[]): TypeExpr {
  return axes.reduceRight<TypeExpr>((inner, axis) => wrapArray(inner, axis), element);
}

/** Never pop content axes or silently treat a scalar as an array. */
export function unwrapArrays(type: TypeExpr, depth = 1): TypeResult {
  if (!Number.isSafeInteger(depth) || depth < 0) return { ok: false, reason: 'invalid-depth', type };
  let current = type;
  for (let i = 0; i < depth; i++) {
    if (current.kind !== 'array') {
      return { ok: false, reason: current.kind === 'unknown' ? 'unknown-type' : 'not-array', type };
    }
    current = current.element;
  }
  return { ok: true, type: current };
}

/** Broadcasting changes participation, not the value's type. Even an array
 * can be broadcast whole. `aggregate` here means the wire value, not N copies. */
export function broadcastInput(type: TypeExpr): TypeViews {
  return { participation: 'broadcast-input', invocation: type, aggregate: type };
}

/** Input MUST be normalized invocation type, never raw PortSpec or an already
 * aggregate resolved_outputs value. Does not implement legacy manifest policy. */
export function viewsFromInvocation(type: TypeExpr, participation: Participation, axis: Axis = { label: null }): TypeViews {
  if (participation === 'broadcast-input') return broadcastInput(type);
  return { participation, invocation: type,
    aggregate: participation === 'none' ? type : wrapArray(type, axis) };
}

/** Resolve the shard view from a known collection; broadcast is deliberately
 * a separate function so an unknown/non-array cannot accidentally be popped. */
export function invocationFromAggregate(type: TypeExpr): TypeResult {
  return unwrapArrays(type);
}

export function unknownRuntime(handleId: string | null, reason: string): RuntimeShape {
  return { handleId, tags: { evidence: { status: 'unknown', reason } },
    axes: { evidence: { status: 'unknown', reason } }, internalCounts: [] };
}
