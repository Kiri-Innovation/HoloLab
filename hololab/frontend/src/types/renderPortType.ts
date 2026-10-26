/** The sole type formatter. Display strings terminate here; callers must never
 * feed them back into type transformations. */
import type { Observation, RuntimeShape, TypeExpr } from './portTypeIR';

export function observedValue<T>(value: Observation<T>): T | undefined {
  return value.evidence.status === 'unknown' ? undefined : value.evidence.value;
}

function axisSizeText(axis: import('./portTypeIR').ObservedAxis): string {
  const value = observedValue(axis.size);
  if (value != null) return String(value);
  const range = axis.sizeRange ? observedValue(axis.sizeRange) : undefined;
  if (range) return range.min === range.max ? String(range.min) : `${range.min}–${range.max}`;
  return axis.size.evidence.status === 'unknown' && axis.size.evidence.reason === 'ragged'
    ? 'varies' : 'unmeasured';
}

export type RenderOptions = {
  mode: 'declaration' | 'long' | 'compact' | 'runtime';
  runtime?: RuntimeShape;
  unknown?: string;
};

export function renderPortType(type: TypeExpr, options: RenderOptions): string {
  const unknown = options.unknown ?? (options.mode === 'compact' ? '?' : 'unknown');
  if (options.mode === 'long') {
    const base = renderPortType(type, { mode: 'declaration', unknown });
    const runtime = options.runtime;
    if (!runtime) return base;
    const counts = runtime.internalCounts.flatMap(item => {
      const value = observedValue(item.count);
      return value == null || (item.key && value === 0) ? []
        : [item.key ? `${item.key}:${value}` : `${value} ${runtime.legacyCountKind ?? 'count'}`];
    });
    const sizes = [...(observedValue(runtime.axes) ?? [])].reverse().map(axis => {
      const value = axisSizeText(axis);
      return axis.label ? `${value} ${axis.label}` : value;
    });
    return base + (counts.length ? ` · ${counts.join(', ')}` : '') + (sizes.length ? ` · ${sizes.join(', ')}` : '');
  }
  if (options.mode === 'declaration') {
    if (type.kind === 'unknown') return unknown;
    if (type.kind === 'union') return type.variants.map(variant => renderPortType(variant, options)).join(' | ');
    if (type.kind === 'array') {
      const axis = type.axis.label ? `[${type.axis.label}]` : '';
      return `arrayed${axis}<${renderPortType(type.element, options)}>`;
    }
    return type.tags.join(',') + type.contentAxes.map(axis => `[${axis.label ?? '?'}]`).join('');
  }
  const runtime = options.runtime;
  if (type.kind === 'union') return type.variants.map(variant => renderPortType(variant, options)).join(' | ');
  let leaf: TypeExpr = type;
  const typeAxes: Array<{ label: string | null }> = [];
  while (leaf.kind === 'array') { typeAxes.push(leaf.axis); leaf = leaf.element; }
  if (leaf.kind === 'union') return leaf.variants.map(variant => renderPortType(variant, options)).join(' | ');
  const knownTags = (runtime ? observedValue(runtime.tags) : undefined)
    ?? (options.mode === 'compact' && leaf.kind === 'object' ? leaf.tags : undefined);
  if (!knownTags?.length && options.mode !== 'compact') return unknown;
  const tags = knownTags?.length ? knownTags : [unknown];
  const counts = runtime?.internalCounts ?? [];
  const visible = counts.map(item => ({ key: item.key, value: observedValue(item.count) }))
    .filter(item => item.value != null && (!item.key || item.value !== 0));
  const countText = visible.length ? `(${visible.map(item => item.key ? `${item.key}:${item.value}` : item.value).join(' ')})` : '';
  const axes = runtime ? observedValue(runtime.axes) : undefined;
  // Unknown measured shape is not silently replaced by a declaration.
  const dimensions = axes?.map(axis => ({ label: axis.label, size: axisSizeText(axis) }))
    ?? (options.mode === 'compact' ? typeAxes.map(axis => ({ label: axis.label, size: '?' })) : undefined);
  const suffix = [...(dimensions ?? [])].reverse().map(axis =>
    `[${axis.label ? `${axis.label}:` : ''}${axis.size}]`).join('');
  if (options.mode === 'compact') {
    const tag = tags.length === 1 ? tags[0] : `${tags[0]}+${tags.length - 1}`;
    return tag + countText + suffix;
  }
  return tags.map(tag => tag + countText + suffix).join(', ');
}
