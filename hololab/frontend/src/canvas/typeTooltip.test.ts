import {measured} from '../test/measurements';
import {expect,it} from 'vitest';
import {typeTooltip} from './typeTooltip';
const declared={tags:['any'],arrayed:true,dimLabels:['frame']};
it('keeps declaration independent of runtime and renders exactly three fields',()=>{
 const result=typeTooltip('images',declared,{tags:['image','rgb'],arrayed:true,dimLabels:['frame'],runtimeAvailable:true,runtimeShape:measured(['image','rgb'],['frame'],[100])});
 expect(result['data-tooltip'].split('\n')).toEqual(['images','arrayed[frame]<any>','image[frame:100], rgb[frame:100]']);
});
it('never presents a declaration or failed summary as real data',()=>{
 expect(typeTooltip('images',declared,{...declared,runtimeAvailable:false})['data-tooltip-actual']).toBe('未知');
});

it('renders an input union as one declared type',()=>{
 const raw={tags:[],arrayed:false,dim_labels:[],any_of:[
  {tags:['colmap4d'],arrayed:false,dim_labels:[]},
  {tags:['colmap-folder'],arrayed:true,dim_labels:['frame']},
 ]};
 const tip=typeTooltip('colmap_4d',{tags:[],arrayed:false,dimLabels:[]},
  {tags:[],arrayed:false,dimLabels:[],runtimeAvailable:false},undefined,raw);
 expect(tip['data-tooltip-declared']).toBe('colmap4d | arrayed[frame]<colmap-folder>');
});

it('removes exactly one outer dimension and keeps internal counts',()=>{
 const actual={tags:['image'],arrayed:true,dimLabels:['frame','cam'],dimSizes:[100,21],runtimeAvailable:true,runtimeShape:measured(['image'],['frame','cam'],[100,21])};
 expect(typeTooltip('frames',declared,actual,{fanout:true,direction:'input'})['data-tooltip-actual']).toBe('image[cam:21]');
 expect(actual.dimSizes).toEqual([100,21]);
 expect(typeTooltip('frames',declared,{...actual,dimLabels:['batch','frame','cam'],runtimeShape:measured(['image'],['batch','frame','cam'],[2,100,21])},{fanout:true,direction:'input'})['data-tooltip-actual']).toBe('image[cam:21][frame:100]');
 expect(typeTooltip('points',declared,{...actual,tags:['point-cloud'],dimLabels:['frame'],runtimeShape:measured(['point-cloud'],['frame'],[100],[{key:'pt',value:7519}])},{fanout:true,direction:'input'})['data-tooltip-actual']).toBe('point-cloud(pt:7519)');
});
it('keeps broadcast scalars and unknown inner counts intact',()=>{
 const scalar={tags:['colmap-cams'],arrayed:false,dimLabels:[],internalCountItems:[{label:'pose',value:21}],runtimeAvailable:true,runtimeShape:measured(['colmap-cams'],[],[],[{key:'pose',value:21}])};
 expect(typeTooltip('cams',scalar,scalar)['data-tooltip-actual']).toBe('colmap-cams(pose:21)');
 expect(typeTooltip('frames',declared,{...scalar,tags:['image'],arrayed:true,dimLabels:['frame','cam'],runtimeShape:measured(['image'],['frame','cam'],[100,undefined])},{fanout:true,direction:'input'})['data-tooltip-actual']).toBe('image[cam:unmeasured]');
});
it('fanout broadcast retains an entire array handle, while split input takes one element',()=>{
 const actual={tags:['image'],arrayed:true,dimLabels:['frame','cam'],dimSizes:[100,21],runtimeAvailable:true,runtimeShape:measured(['image'],['frame','cam'],[100,21])};
 const scalar={tags:['image'],arrayed:false,dimLabels:[]};
 const broadcast=typeTooltip('frames',scalar,actual,{fanout:true,direction:'input',broadcast:true});
 expect(broadcast['data-tooltip-actual']).toBe('image[cam:21][frame:100]');
 expect(broadcast['data-tooltip-after-declared']).toBe('image');
 expect(broadcast['data-tooltip-after-title']).toBe('arrayed (auto-popped)');
 expect(broadcast['data-tooltip-after-actual']).toBe('image[cam:21][frame:100]');
 expect(typeTooltip('frames',declared,actual,{fanout:true,direction:'input'})['data-tooltip-actual']).toBe('image[cam:21]');
 expect(typeTooltip('frames',declared,{...actual,runtimeAvailable:false},{fanout:true,direction:'output'})['data-tooltip-after-actual']).toBe('未知');
});

it('does not rewrap aggregate declarations and keeps full runtime on the right',()=>{
 const actual={tags:['image'],arrayed:true,dimLabels:['frame','cam'],dimSizes:[100,21],runtimeAvailable:true,runtimeShape:measured(['image'],['frame','cam'],[100,21])};
 const type={tags:['image'],arrayed:true,dimLabels:['']};
 const result=typeTooltip('frames',type,actual,{fanout:true,direction:'input'});
 expect(result['data-tooltip'].split('\n')).toEqual(['frames','arrayed<image>','image[cam:21]']);
 expect(result['data-tooltip-after-declared']).toBe('arrayed<image>');
 expect(result['data-tooltip-after-actual']).toBe('image[cam:21][frame:100]');
 expect(typeTooltip('frames',{...type,dimLabels:['','']},actual,{fanout:true,direction:'input'})['data-tooltip-after-declared']).toBe('arrayed<arrayed<image>>');
 expect(typeTooltip('frames',type,actual)['data-tooltip-after-declared']).toBeUndefined();
 expect(typeTooltip('frames',type,{...actual,arrayed:false,dimLabels:[],runtimeShape:measured(['image'])},{fanout:true,direction:'input'})['data-tooltip-after-actual']).toBe('未知（无 arrayed 维度）');
});

it('broadcast uses unchanged scalar values in two columns only in fanout context',()=>{
 const scalar={tags:['colmap-cams'],arrayed:false,dimLabels:[],internalCountItems:[{label:'pose',value:21},{label:'intr',value:1}],runtimeAvailable:true,runtimeShape:measured(['colmap-cams'],[],[],[{key:'pose',value:21},{key:'intr',value:1}])};
 const view={fanout:true,direction:'input' as const,broadcast:true};
 const result=typeTooltip('cams',scalar,scalar,view);
 expect(result['data-tooltip-after-title']).toBe('arrayed (auto-popped)');
 expect(result['data-tooltip-after-declared']).toBe('colmap-cams');
 expect(result['data-tooltip-after-actual']).toBe('colmap-cams(pose:21 intr:1)');
 expect(typeTooltip('cams',scalar,scalar,{...view,fanout:false})['data-tooltip-after-title']).toBeUndefined();
 expect(typeTooltip('cams',scalar,{...scalar,runtimeAvailable:false},view)['data-tooltip-after-actual']).toBe('未知');
});

it('old numeric fields never fill missing runtime evidence in either column',()=>{
 const old={...declared,tags:['image'],dimSizes:[100,21],internalCountItems:[{label:'pose',value:21}],runtimeAvailable:true};
 const tip=typeTooltip('frames',declared,old,{fanout:true,direction:'input'});
 expect(tip['data-tooltip-actual']).toBe('未知');
 expect(tip['data-tooltip-after-actual']).toBe('未知');
});
