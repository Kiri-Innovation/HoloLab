import {expect,it} from 'vitest';
import {typeTooltip} from './typeTooltip';
const declared={tags:['any'],arrayed:true,dimLabels:['frame']};
it('keeps declaration independent of runtime and renders exactly three fields',()=>{
 const result=typeTooltip('images',declared,{tags:['image','rgb'],arrayed:true,dimLabels:['frame'],dimSizes:[100],runtimeAvailable:true});
 expect(result['data-tooltip'].split('\n')).toEqual(['images','arrayed<frame> of any','image[frame:100], rgb[frame:100]']);
});
it('never presents a declaration or failed summary as real data',()=>{
 expect(typeTooltip('images',declared,{...declared,runtimeAvailable:false})['data-tooltip-actual']).toBe('未知');
});
