import { formatTypeLabel, formatTypeLabelLong, type EdgeType } from './edgeLabels';

/** Runtime dimensions are outer-first. Describe one element, not the whole
 * collection. Keep inner dimensions and internal counts; never mutate chip data. */
export function elementType(actual: EdgeType): EdgeType {
  if (!actual.arrayed && !actual.dimLabels.length && !actual.dimSizes?.length) return actual;
  const dimLabels = actual.dimLabels.slice(1);
  const dimSizes = actual.dimSizes?.slice(1);
  return { ...actual, dimLabels, dimSizes,
    arrayed: Math.max(dimLabels.length, dimSizes?.length ?? 0) > 0 || actual.innerElementCount != null,
    elementCount: dimSizes?.[0] ?? actual.innerElementCount,
    innerElementCount: dimSizes?.[1],
  };
}

/** Keep all tags (the compact edge chip intentionally abbreviates them). */
export function typeTooltip(name: string, declared: EdgeType, actual: EdgeType & { runtimeAvailable?: boolean }, view?: { fanout: boolean; direction: "input" | "output"; broadcast?: boolean }) {
  const declaration = formatTypeLabelLong({ tags: declared.tags, arrayed: declared.arrayed, dimLabels: declared.dimLabels });
  const element = view?.fanout && view.broadcast ? actual : elementType(actual);
  const runtime = actual.runtimeAvailable
    ? actual.tags.map(tag => formatTypeLabel({ ...element, tags: [tag] })).join(', ') || '未知'
    : '未知';
  // A node toggle adds one collection layer to the left-hand view. Scalar
  // broadcast ports are not wrapped: every invocation sees the same bundle.
  const afterTitle = view?.fanout ? view.broadcast ? 'arrayed (auto-popped)' : 'after arrayed' : undefined;
  const afterDeclared = view?.fanout ? view.broadcast ? declaration : `arrayed<${declaration}>` : undefined;
  const fullRuntime = !actual.runtimeAvailable ? '未知'
    : view?.broadcast || actual.arrayed || actual.dimLabels.length || actual.dimSizes?.length
      ? actual.tags.map(tag => formatTypeLabel({ ...actual, tags: [tag] })).join(', ') || '未知'
      : '未知（无 arrayed 维度）';
  return {
    'data-tooltip-after-title': afterTitle,
    'data-tooltip-after-declared': afterDeclared,
    'data-tooltip-after-actual': afterDeclared ? fullRuntime : undefined,
    'data-tooltip': `${name}\n${declaration}\n${runtime}`,
    'data-tooltip-type': name,
    'data-tooltip-declared': declaration,
    'data-tooltip-actual': runtime,
    'aria-label': `变量名：${name}；类型：${declaration}；元素实际类型：${runtime}${afterDeclared ? `；${afterTitle}：${afterDeclared}；完整实际类型：${fullRuntime}` : ''}`,
  };
}
