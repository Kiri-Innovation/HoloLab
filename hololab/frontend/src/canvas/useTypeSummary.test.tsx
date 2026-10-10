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
