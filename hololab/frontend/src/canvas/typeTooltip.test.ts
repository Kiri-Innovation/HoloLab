import {expect,it} from 'vitest';
import {typeTooltip} from './typeTooltip';
const declared={tags:['any'],arrayed:true,dimLabels:['frame']};
it('keeps declaration independent of runtime and renders exactly three fields',()=>{
 const result=typeTooltip('images',declared,{tags:['image','rgb'],arrayed:true,dimLabels:['frame'],dimSizes:[100],runtimeAvailable:true});
 expect(result['data-tooltip'].split('\n')).toEqual(['images','arrayed<frame> of any','image, rgb']);
});
it('never presents a declaration or failed summary as real data',()=>{
 expect(typeTooltip('images',declared,{...declared,runtimeAvailable:false})['data-tooltip-actual']).toBe('未知');
});

it('removes exactly one outer dimension and keeps internal counts',()=>{
 const actual={tags:['image'],arrayed:true,dimLabels:['frame','cam'],dimSizes:[100,21],runtimeAvailable:true};
 expect(typeTooltip('frames',declared,actual)['data-tooltip-actual']).toBe('image[cam:21]');
 expect(actual.dimSizes).toEqual([100,21]);
 expect(typeTooltip('frames',declared,{...actual,dimLabels:['batch','frame','cam'],dimSizes:[2,100,21]})['data-tooltip-actual']).toBe('image[cam:21][frame:100]');
 expect(typeTooltip('points',declared,{...actual,tags:['point-cloud'],dimLabels:['frame'],dimSizes:[100],internalCountItems:[{label:'pt',value:7519}]})['data-tooltip-actual']).toBe('point-cloud(pt:7519)');
});
it('keeps broadcast scalars and unknown inner counts intact',()=>{
 const scalar={tags:['colmap-cams'],arrayed:false,dimLabels:[],internalCountItems:[{label:'pose',value:21}],runtimeAvailable:true};
 expect(typeTooltip('cams',scalar,scalar)['data-tooltip-actual']).toBe('colmap-cams(pose:21)');
 expect(typeTooltip('frames',declared,{...scalar,tags:['image'],arrayed:true,dimLabels:['frame','cam'],internalCountItems:[],elementCount:100})['data-tooltip-actual']).toBe('image[cam:?]');
});
it('fanout broadcast retains an entire array handle, while split input takes one element',()=>{
 const actual={tags:['image'],arrayed:true,dimLabels:['frame','cam'],dimSizes:[100,21],runtimeAvailable:true};
 const scalar={tags:['image'],arrayed:false,dimLabels:[]};
 const broadcast=typeTooltip('frames',scalar,actual,{fanout:true,direction:'input',broadcast:true});
 expect(broadcast['data-tooltip-actual']).toBe('image[cam:21][frame:100]');
 expect(broadcast['data-tooltip-after-declared']).toBeUndefined();
 expect(typeTooltip('frames',declared,actual,{fanout:true,direction:'input'})['data-tooltip-actual']).toBe('image[cam:21]');
 expect(typeTooltip('frames',declared,{...actual,runtimeAvailable:false},{fanout:true,direction:'output'})['data-tooltip-after-actual']).toBe('未知');
});

it('adds precisely one wrapper and shows the complete unmodified runtime on the right',()=>{
 const actual={tags:['image'],arrayed:true,dimLabels:['frame','cam'],dimSizes:[100,21],runtimeAvailable:true};
 const type={tags:['image'],arrayed:true,dimLabels:['']};
 const result=typeTooltip('frames',type,actual,{fanout:true,direction:'input'});
 expect(result['data-tooltip'].split('\n')).toEqual(['frames','arrayed<image>','image[cam:21]']);
 expect(result['data-tooltip-after-declared']).toBe('arrayed<arrayed<image>>');
 expect(result['data-tooltip-after-actual']).toBe('image[cam:21][frame:100]');
 expect(typeTooltip('frames',{...type,dimLabels:['','']},actual,{fanout:true,direction:'input'})['data-tooltip-after-declared']).toBe('arrayed<arrayed<arrayed<image>>>');
 expect(typeTooltip('frames',type,actual)['data-tooltip-after-declared']).toBeUndefined();
 expect(typeTooltip('frames',type,{...actual,arrayed:false,dimLabels:[],dimSizes:[]},{fanout:true,direction:'input'})['data-tooltip-after-actual']).toBe('未知（无 arrayed 维度）');
});
