import { describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { CanvasContext } from './CanvasContext';
import { InlinePortCard } from './CanvasInlineValues';
const view = vi.hoisted(() => ({ zoom: 1 }));
vi.mock('@xyflow/react', () => ({useStore: (select: (s: unknown) => unknown) => select({transform:[0,0,view.zoom]})}));
import { activeInlineValue, inlineEditorInitiallyExpanded, inlineType, toggledInlineEditor } from './CanvasInlineValues';
import type { InputPortSpec } from '../wire';
const spec = {tags:['float'],storage:'file',required:true} as InputPortSpec;
describe('port inline cards', () => {
  it('only exposes active supported values, preserving zero', () => {
    expect(activeInlineValue(spec,{offset:0},'offset',false)).toBe(0);
    expect(activeInlineValue(spec,{offset:2},'offset',true)).toBeUndefined();
    expect(activeInlineValue({...spec,tags:['int']},{offset:2},'offset',false)).toBeUndefined();
    expect(activeInlineValue(spec,{},'offset',false)).toBeUndefined();
  });
  it('keeps frozen snapshot values distinct from current defaults', () => {
    const defaults = {...spec,inline_default:3};
    expect(activeInlineValue(defaults,{},'offset',false)).toBe(3);
    expect(activeInlineValue(defaults,{},'offset',false,true)).toBeUndefined();
  });
  it('formats scalar, compound and empty array types', () => {
    expect(inlineType(spec,0,false)).toBe('float');
    expect(inlineType({...spec,tags:['vec3']},[[0,0,1],[1,1,1]],true)).toBe('vec3[2]');
    expect(inlineType(spec,[],true)).toBe('float[0]');
  });
  it('does not read array elements, regardless of length', () => {
    const value = new Proxy(new Array(100000), {get(target,key) {
      if (key !== 'length') throw Error('Must not traverse array');
      return target.length;
    }});
    expect(inlineType(spec,value,true)).toBe('float[100000]');
  });
});

describe('port card rendering', () => {
  const render = (array = false, frozen = false, value: unknown = array ? [0,2] : 0) => renderToStaticMarkup(
    <CanvasContext.Provider value={{workflow_id:null,workflow_name:'test',computeNodesById:{},hydrating:false,
      viewer_view:null,viewer_view_loaded:false,viewer_view_managed:false,
      onInlineValueChange:()=>{},onInspectInput:()=>{}}}>
      <InlinePortCard id="node" name="offset" spec={spec} value={value} array={array} frozen={frozen}/>
    </CanvasContext.Provider>);
  it('starts expanded for a value and collapses when the icon is toggled', () => {
    expect(inlineEditorInitiallyExpanded(0)).toBe(true);
    expect(toggledInlineEditor(true)).toBe(false);
    expect(toggledInlineEditor(false)).toBe(true);
    expect(render()).toContain('type="number"');
    expect(render()).toContain('value="0"');
    expect(render()).toContain('aria-expanded="true"');
    expect(render()).toContain('已设置内联值');
  });
  it('starts collapsed without a value and labels that state for assistive technology', () => {
    expect(inlineEditorInitiallyExpanded(undefined)).toBe(false);
    expect(render(false, false, null)).toContain('aria-expanded="false"');
    expect(render(false, false, null)).toContain('没有内联值');
    expect(render(false, false, null)).not.toContain('type="number"');
  });
  it('uses an editable numeric control for a float and a pencil action for arrays', () => {
    expect(render(true)).toContain('float[2]');
    expect(render(true)).toContain('Edit offset in Inspector');
    expect(render(true)).not.toContain('type="number"');
    expect(render(true)).not.toContain('Show more');
  });
  it('keeps historical scalar values read-only', () => {
    expect(render(false,true)).toContain('<fieldset disabled=""');
  });
  it.each([1, .5, .25])('keeps scalar and array cards at zoom %s', zoom => {
    view.zoom=zoom;
    expect(render()).toContain('type="number"');
    expect(render(true)).toContain('float[2]');
    view.zoom=1;
  });
});
