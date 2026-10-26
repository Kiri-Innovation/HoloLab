import type { EdgeType } from './edgeLabels';
import type { PortDeclaration } from '../types/portTypeIR';
import { adaptPortType, declarationType, elementRuntime, type PortContext } from '../types/legacyPortType';
import { observedValue, renderPortType } from '../types/renderPortType';

/** Data assembly only: every type label is a terminal IR render. Effective
 * legacy fields are already aggregate; never wrap a rendered declaration. */
export function typeTooltip(name: string, declared: EdgeType, actual: EdgeType & { runtimeAvailable?: boolean },
  view: PortContext = { fanout: false, direction: 'input' }, raw?: PortDeclaration) {
  const source = raw ?? { tags: declared.tags, arrayed: declared.arrayed, dim_labels: declared.dimLabels };
  const effective = raw?.any_of?.length && actual.runtimeAvailable ? actual : declared;
  const ir = adaptPortType(source, effective, { ...actual, runtimeAvailable: Boolean(actual.runtimeAvailable) }, view, view.handleId ?? null);
  const original = raw ? declarationType(raw) : ir.views.aggregate;
  const declaration = raw?.any_of?.length
    ? raw.any_of.map(variant => renderPortType(declarationType({
      tags: [...variant.tags], arrayed: Boolean(variant.arrayed), scalar: false,
      dim_labels: [...(variant.dim_labels ?? [])],
    }), { mode: 'declaration' })).join(' | ')
    : renderPortType(original, { mode: 'declaration' });
  const broadcast = ir.views.participation === 'broadcast-input';
  const item = !view.fanout || broadcast ? ir.runtime : elementRuntime(ir.runtime);
  const runtime = renderPortType(ir.views.invocation, { mode: 'runtime', runtime: item, unknown: '未知' });
  const afterTitle = view.fanout ? broadcast ? 'arrayed (auto-popped)' : 'after arrayed' : undefined;
  const afterDeclared = view.fanout ? renderPortType(ir.views.aggregate, { mode: 'declaration' }) : undefined;
  const axes = observedValue(ir.runtime.axes);
  const fullRuntime = !actual.runtimeAvailable || !actual.runtimeShape ? '未知' : !broadcast && !axes?.length ? '未知（无 arrayed 维度）'
    : renderPortType(ir.views.aggregate, { mode: 'runtime', runtime: ir.runtime, unknown: '未知' });
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
