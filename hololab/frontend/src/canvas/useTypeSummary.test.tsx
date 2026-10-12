import {afterEach,expect,it} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {useTypeSummary,mergeTypeSummary} from './useTypeSummary';
import {formatTypeLabel,formatTypeLabelLong} from './edgeLabels';
import {_resetEdgeSummaryCache,seedEdgeSummaryFacts,factsFromSummary} from './edgeSummaryCache';
import {measuredPayload} from '../test/measurements';
function Summary({id}:{id?:string}) {
 const type=useTypeSummary({tags:['image','rgb'],arrayed:true,dimLabels:['static']},id);
 return <span>{formatTypeLabelLong(type)}</span>;
}
afterEach(_resetEdgeSummaryCache);
it('uses new evidence without leaking it across handles',()=>{
 seedEdgeSummaryFacts('a',{handle_id:'a',tags:['image','rgb'],dim_labels:['frame'],deleted:false,
  runtime_shape:{...measuredPayload(['image','rgb'],['frame'],[100],[{key:'',value:12}],'a'),legacyCountKind:'pixels'}});
 const actual=renderToStaticMarkup(<Summary id="a"/>);
 expect(actual).toContain('100 frame');expect(actual).toContain('12 pixels');
 expect(renderToStaticMarkup(<Summary id="b"/>)).not.toContain('100');
 expect(renderToStaticMarkup(<Summary/>)).not.toContain('100');
});
it('ignores legacy inline counts and unavailable evidence safely',()=>{
 seedEdgeSummaryFacts('old',{handle_id:'old',tags:['image'],dim_sizes:[999],dim_labels:['frame'],internal_count:77,deleted:false});
 const actual=renderToStaticMarkup(<Summary id="old"/>);
 expect(actual).toContain('static');expect(actual).not.toContain('999');expect(actual).not.toContain('77');
 expect(factsFromSummary({handle_id:'h',runtime_shape:{schemaVersion:2}})).toEqual({});
 expect(factsFromSummary({handle_id:'h',runtime_shape:measuredPayload(['image'],[],[],[],'other')})).toEqual({});
});
it('renders only structured measured dimensions, including unknown sizes',()=>{
 const type={tags:['image'],arrayed:true,dimLabels:[]};
 const facts=factsFromSummary({handle_id:'h',runtime_shape:measuredPayload(['image'],['batch','frame','cam'],[2,3,undefined],[],'h')});
 expect(formatTypeLabel(mergeTypeSummary(type,facts))).toBe('image[cam:?][frame:3][batch:2]');
});
