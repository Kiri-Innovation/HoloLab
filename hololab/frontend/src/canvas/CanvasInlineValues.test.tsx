import { describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { CanvasContext } from './CanvasContext';
import { InlinePortCard } from './CanvasInlineValues';
const view = vi.hoisted(() => ({ zoom: 1 }));
vi.mock('@xyflow/react', () => ({useStore: (select: (s: unknown) => unknown) => select({transform:[0,0,view.zoom]})}));
import { activeInlineValue, inlineType } from './CanvasInlineValues';
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
  const render = (array = false, frozen = false) => renderToStaticMarkup(
    <CanvasContext.Provider value={{workflow_id:null,workflow_name:'test',computeNodesById:{},hydrating:false,
      viewer_view:null,viewer_view_loaded:false,viewer_view_managed:false,
      onInlineValueChange:()=>{},onInspectInput:()=>{}}}>
      <InlinePortCard id="node" name="offset" spec={spec} value={array ? [0,2] : 0} array={array} frozen={frozen}/>
    </CanvasContext.Provider>);
  it('uses an editable numeric control for a float and a pencil action for arrays', () => {
    expect(render()).toContain('type="number"');
    expect(render()).toContain('value="0"');
    expect(render(true)).toContain('float[2]');
    expect(render(true)).toContain('Edit offset in Inspector');
    expect(render(true)).not.toContain('type="number"');
    expect(render(true)).not.toContain('Show more');
  });
  it('keeps historical scalar values read-only', () => {
    expect(render(false,true)).toContain('<fieldset disabled=""');
  });
  it('hides cards at 50%, shows them at 80%', () => {
    view.zoom=.5;
    expect(render()).toBe('');
    view.zoom=.8;
    expect(render()).toContain('hl-inline-port-card');
    view.zoom=1;
  });
});
