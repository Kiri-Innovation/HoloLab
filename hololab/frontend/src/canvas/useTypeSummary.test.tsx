import { afterEach, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { useTypeSummary } from './useTypeSummary';
import { formatTypeLabelLong } from './edgeLabels';
import { _resetEdgeSummaryCache, seedEdgeSummaryFacts } from './edgeSummaryCache';
function Summary({id}:{id?:string}) {
  const type=useTypeSummary({tags:['image','rgb'],arrayed:true,dimLabels:['static']},id);
  return <span>{formatTypeLabelLong(type)}</span>;
}
afterEach(_resetEdgeSummaryCache);
it('uses shared runtime dimensions and internal counts without leaking facts to another handle',()=>{
  seedEdgeSummaryFacts('a',{handle_id:'a',tags:['image','rgb'],dim_labels:['frame'],dim_sizes:[100],internal_count:12,internal_count_kind:'pixels',deleted:false});
  const actual=renderToStaticMarkup(<Summary id="a"/>);
  expect(actual).toContain('frame');expect(actual).toContain('100 frame');expect(actual).toContain('12 pixels');
  const unknown=renderToStaticMarkup(<Summary id="b"/>);
  expect(unknown).toContain('static');expect(unknown).not.toContain('100');
  expect(renderToStaticMarkup(<Summary/>)).not.toContain('100');
});

import { mergeTypeSummary } from './useTypeSummary';
import { formatTypeLabel } from './edgeLabels';
it('preserves unnamed arrays and never mistakes internal pose counts for array lengths',()=>{
 const scalar={tags:['colmap-cams'],arrayed:false,dimLabels:[]};
 const facts={tags:['colmap-cams'],dimLabels:[],internalCountItems:[{label:'pose',value:21},{label:'intr',value:1}]};
 expect(formatTypeLabel(mergeTypeSummary(scalar,facts))).toBe('colmap-cams(pose:21 intr:1)');
 expect(formatTypeLabel(mergeTypeSummary({...scalar,arrayed:true}, {...facts,elementCount:100}))).toBe('colmap-cams(pose:21 intr:1)[100]');
 expect(formatTypeLabel(mergeTypeSummary({...scalar,arrayed:true},facts))).toBe('colmap-cams(pose:21 intr:1)[?]');
});
it('renders all authoritative dimensions, inner to outer',()=>{
 const type=mergeTypeSummary({tags:['image'],arrayed:true,dimLabels:[]},{tags:['image'],dimSizes:[2,3,4],dimLabels:['batch','frame','cam']});
 expect(formatTypeLabel(type)).toBe('image[cam:4][frame:3][batch:2]');
});
