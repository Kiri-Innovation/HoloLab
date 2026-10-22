import { memo } from 'react';
import { useStore } from '@xyflow/react';
import type { InputPortSpec } from '../wire';
import { inputValue, supportsInline } from './inlineValues';
import { ParameterInput } from './ParameterInput';
import { useCanvasContext } from './CanvasContext';
import './canvasInlineValues.css';

export function activeInlineValue(spec: InputPortSpec, values: Record<string, unknown> | undefined, name: string, wired: boolean, frozen = false) {
  return wired || !supportsInline(spec) ? undefined : frozen ? values?.[name] : inputValue(spec, values ?? {}, name);
}

/** Only inspect length, never traverse or stringify an array on the canvas. */
export function inlineType(spec: InputPortSpec, value: unknown, array: boolean): string {
  return `${spec.tags[0]}${array ? `[${Array.isArray(value) ? value.length : '?'}]` : ''}`;
}

export const InlinePortCard = memo(function InlinePortCard({ id, name, spec, value, array, frozen = false }: {
  id: string; name: string; spec: InputPortSpec; value: unknown; array: boolean; frozen?: boolean;
}) {
  const hidden = useStore(s => s.transform[2] < 0.6);
  const { onInlineValueChange, onInspectInput } = useCanvasContext();
  if (hidden) return null;
  const scalar = !array && spec.tags[0] === 'float';
  return <div className="hl-inline-port-card nodrag nopan nowheel" aria-label={`${name} inline value`}
    onKeyDown={e => e.stopPropagation()} onPointerDown={e => e.stopPropagation()}
    onDoubleClick={e => e.stopPropagation()} onClick={e => e.stopPropagation()}>
    {scalar ? <><code>float</code><fieldset disabled={frozen || !onInlineValueChange}>
      <ParameterInput name={`${name} inline`} spec={{type:'float', default:null, description:null, optional:false}}
        value={value} nodeId={null} initialDirectory=""
        onChange={next => onInlineValueChange?.(id, name, next)} />
    </fieldset></> : <button type="button" title="点击在 Inspector 编辑" aria-label={`Edit ${name} in Inspector`}
      disabled={!onInspectInput} onClick={() => onInspectInput?.(id, name)}>
      <code>{inlineType(spec, value, array)}</code>
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="m15 5 4 4M4 20l4-1L20 7a2.8 2.8 0 0 0-4-4L4 15v5Z"/></svg>
    </button>}
  </div>;
});
