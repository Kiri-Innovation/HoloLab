/** Explicit phase-2 bridge. Legacy fields are data, never formatted labels.
 * Effective/resolved fields already describe the aggregate wire value. */
import type { EdgeType } from '../canvas/edgeLabels';
import { broadcastInput, objectType, unknownRuntime, unknownType, unwrapArrays, viewsFromInvocation, wrapArrays,
  type Observation, type PortDeclaration, type PortTypeIR, type RuntimeShape, type TypeExpr, type TypeViews } from './portTypeIR';

export type LegacyFacts = EdgeType & { runtimeAvailable?: boolean };
export type PortContext = { fanout: boolean; direction: 'input' | 'output'; broadcast?: boolean; handleId?: string | null };

export function typeFromLegacy(t: Pick<EdgeType, 'tags' | 'arrayed' | 'dimLabels'>): TypeExpr {
  // A scalar's named axes describe content, not collection wrappers.
  if (!t.arrayed) return objectType(t.tags, t.dimLabels.map(label => ({ label: label || null })));
  const depth = Math.max(1, t.dimLabels.length);
  return wrapArrays(objectType(t.tags), Array.from({ length: depth }, (_, i) => ({ label: t.dimLabels[i] || null })));
}

/** Raw declaration is distinct from effective resolved fields. A scalar flag
 * doesn't erase output aggregate dimensions; aggregate is supplied separately. */
export function declarationType(port: PortDeclaration): TypeExpr {
  return typeFromLegacy({ tags: port.tags, arrayed: Boolean(port.arrayed),
    dimLabels: port.arrayed ? port.dim_labels ?? [] : [] });
}

/** The source must state whether it is invocation or already aggregate. */
export function legacyViews(type: TypeExpr, context: PortContext, basis: 'invocation' | 'aggregate'): TypeViews {
  const mode = !context.fanout ? 'none' : context.broadcast && context.direction === 'input'
    ? 'broadcast-input' : context.direction === 'input' ? 'map-input' : 'collect-output';
  if (basis === 'invocation') return viewsFromInvocation(type, mode);
  if (mode === 'broadcast-input') return broadcastInput(type);
  if (mode === 'none') return viewsFromInvocation(type, mode);
  const item = unwrapArrays(type);
  return { participation: mode, aggregate: type,
    invocation: item.ok ? item.type : unknownType(`aggregate-${item.reason}`) };
}

function legacy<T>(value: T | undefined): Observation<T> {
  return { evidence: { status: 'unknown', reason: 'legacy-coverage-unrecorded' },
    ...(value === undefined ? {} : { legacyValue: value }) };
}

/** Preserve old measurements without claiming a complete scan. dim_sizes wins
 * per supplied axis; legacy element_count is only a missing-field fallback. */
export function runtimeFromLegacy(t: LegacyFacts, handleId: string | null = null): RuntimeShape {
  if (t.runtimeAvailable === false) return unknownRuntime(handleId, 'summary-unavailable');
  const depth = Math.max(t.arrayed ? 1 : 0, t.dimLabels.length, t.dimSizes?.length ?? 0);
  const fallback: RuntimeShape = { handleId, legacyCountKind: t.internalCountKind, tags: legacy([...t.tags]), axes: legacy(Array.from({ length: depth }, (_, i) => ({
    label: t.dimLabels[i] || null, role: 'unknown' as const,
    size: legacy(t.dimSizes?.[i] ?? (i === 0 ? t.elementCount : i === 1 ? t.innerElementCount : undefined)),
  }))), internalCounts: t.internalCountItems?.length
    ? t.internalCountItems.map(item => ({ key: item.label, count: legacy(item.value) }))
    : t.internalCount != null ? [{ key: '', count: legacy(t.internalCount) }] : [] };
  const measured = t.runtimeShape;
  if (!measured || handleId && measured.handleId !== handleId) return fallback;
  // Keep unmeasured legacy values without upgrading their evidence. This also
  // preserves old display behavior when a probe cannot determine all axes.
  return { ...measured, axes: measured.axes.evidence.status === 'unknown'
    ? { ...measured.axes, legacyValue: fallback.axes.legacyValue } : measured.axes };
}

/** Compatibility projection for the existing tooltip element row. This is a
 * structural observation projection, not a new observation of every shard.
 * Unknown evidence and legacy sample counts retain their original provenance. */
export function elementRuntime(runtime: RuntimeShape): RuntimeShape {
  const observation = runtime.axes;
  if (observation.evidence.status !== 'unknown') {
    return { ...runtime, axes: { evidence: { ...observation.evidence, value: observation.evidence.value.slice(1) } } };
  }
  return { ...runtime, axes: { ...observation,
    ...(observation.legacyValue ? { legacyValue: observation.legacyValue.slice(1) } : {}) } };
}

export function adaptPortType(declaration: PortDeclaration, effective: EdgeType, actual: LegacyFacts,
  context: PortContext, handleId: string | null = null): PortTypeIR {
  return { schemaVersion: 1, declaration,
    views: legacyViews(typeFromLegacy(effective), context, 'aggregate'),
    runtime: runtimeFromLegacy(actual, handleId) };
}
