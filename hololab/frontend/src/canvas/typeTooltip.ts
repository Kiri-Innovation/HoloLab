import { formatTypeLabel, formatTypeLabelLong, type EdgeType } from './edgeLabels';

/** Keep all tags (the compact edge chip intentionally abbreviates them). */
export function typeTooltip(name: string, declared: EdgeType, actual: EdgeType & { runtimeAvailable?: boolean }) {
  const declaration = formatTypeLabelLong({ tags: declared.tags, arrayed: declared.arrayed, dimLabels: declared.dimLabels });
  const runtime = actual.runtimeAvailable
    ? actual.tags.map(tag => formatTypeLabel({ ...actual, tags: [tag] })).join(', ') || '未知'
    : '未知';
  return {
    'data-tooltip': `${name}\n${declaration}\n${runtime}`,
    'data-tooltip-type': name,
    'data-tooltip-declared': declaration,
    'data-tooltip-actual': runtime,
    'aria-label': `变量名：${name}；类型：${declaration}；实际数据类型：${runtime}`,
  };
}
