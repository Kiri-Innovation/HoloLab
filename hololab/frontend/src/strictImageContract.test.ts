import {expect,it} from 'vitest';
import {effectivePortArrayed,effectivePortDimLabels,portsCompatible} from './tags';
import {effectiveOutputType} from './canvas/edgeLabels';
import type {CatalogPack,GraphNode} from './wire';
it('requires a matching invocation array, even on broadcast inputs',()=>{
 expect(portsCompatible(['image'],true,['image'],false,true,['cam'],[])).toBe(false);
 expect(portsCompatible(['image'],true,['image'],true,false,['frame','cam'],['cam'])).toBe(false);
 expect(portsCompatible(['image'],true,['image'],true,false,['frame','cam'],['','cam'])).toBe(true);
 expect(portsCompatible(['image_sequence'],false,['any'],false)).toBe(false);
 expect(portsCompatible(['any'],false,['frame_sequence'],true)).toBe(false);
});
it('wraps invocation arrays once and preserves broadcast arrays',()=>{
 expect(effectivePortDimLabels(true,['cam'],true,true)).toEqual(['','cam']);
 expect(effectivePortDimLabels(true,['cam'],true,true,true)).toEqual(['cam']);
 expect(effectivePortArrayed(true,true,true,true)).toBe(true);
});
it('resolves aggregate dimensions before generic drop and collection wrapping',()=>{
 const packs = [
  {name:'source',version:'1',arrayable:false,inputs:{},outputs:{out:{tags:['image'],arrayed:true,dim_labels:['frame','cam']}}},
  {name:'und',version:'1',arrayable:true,inputs:{images:{tags:['image'],arrayed:true,dim_labels:['cam']}},outputs:{images:{tags:['image'],arrayed:true,dim_labels:['cam']}}},
  {name:'drop',version:'1',inputs:{arr:{tags:['any'],arrayed:true}},outputs:{out:{tags:['any'],tags_from:'arr',dim_labels_from_input:'arr',dim_labels_drop_outer:1}}},
  {name:'wrap',version:'1',inputs:{data:{tags:['any']}},outputs:{out:{tags:['any'],tags_from:'data',arrayed:true}}},
 ] as unknown as CatalogPack[];
 const nodes=packs.map(p=>({id:p.name,algorithm_name:p.name,algorithm_version:p.version,params:{},arrayed_toggle:p.name==='und'})) as GraphNode[];
 const edges=[['source','out','und','images'],['und','images','drop','arr'],['drop','out','wrap','data']].map(([source,sourceHandle,target,targetHandle],i)=>({id:String(i),source,sourceHandle,target,targetHandle}));
 const ctx={nodes,edges,catalogByKey:new Map(packs.map(p=>[`${p.name}@${p.version}`,p]))};
 expect(effectiveOutputType('und','images',ctx)).toEqual({tags:['image'],arrayed:true,dimLabels:['frame','cam']});
 expect(effectiveOutputType('drop','out',ctx)).toEqual({tags:['image'],arrayed:true,dimLabels:['cam']});
 expect(effectiveOutputType('wrap','out',ctx)).toEqual({tags:['image'],arrayed:true,dimLabels:['','cam']});
});
