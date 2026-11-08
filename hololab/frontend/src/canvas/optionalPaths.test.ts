import {expect,it} from 'vitest';
import {omitEmptyOptionalPaths} from './optionalPaths';
import {mergeParameterPatch} from './PackParameterField';
import {initialParameters} from './initialParameters';
import type {CatalogPack} from '../wire';
const pack={name:'test',version:'1',params:{checkpoint:{type:'path',optional:true,default:null},video_dir:{type:'path',optional:false,default:''},other:{type:'string',optional:true,default:null}}} as unknown as CatalogPack;
it('omits optional path null, blank and cleared edits while retaining required paths and other nulls',()=>{
 for(const value of [null,undefined,'','   '])expect(omitEmptyOptionalPaths(pack.params,{checkpoint:value,video_dir:'',other:null})).toEqual({video_dir:'',other:null});
 expect(initialParameters(pack)).toEqual({video_dir:''});
 expect(mergeParameterPatch(pack,{checkpoint:'/weights',video_dir:'/videos'},{checkpoint:''})).toEqual({video_dir:'/videos'});
 expect(mergeParameterPatch(pack,{video_dir:'/videos'},{checkpoint:'/override'})).toEqual({video_dir:'/videos',checkpoint:'/override'});
});
