import {expect,it} from 'vitest';
import {localOverview} from './localOverviewModel';
import type {PackEntry} from './packManagerModel';
const entry=(key:string,requires:unknown,updated:number):PackEntry=>({key,name:key,version:'1',scope:'test',catalog:{name:key,version:'1',node_ids:['node'],requires} as PackEntry['catalog'],inventory:[],usages:updated?[{workflow:{workflow_id:key,name:key,updated_ts:updated,graph:{nodes:[],edges:[]}},node:{id:key,algorithm_name:key,algorithm_version:"1",position:{x:0,y:0},params:{},assigned_node_id:null},issues:[]}]:[]});
it('separates undeclared, missing, installed and reference-only entries',()=>{
 const missing=entry('missing',{python:'>=3'},1),undeclared=entry('undeclared',null,0),reference=entry('reference',null,0);reference.catalog=undefined;
 const result=localOverview([missing,undeclared,reference],'node',{missing:{status:'missing',issues:[]}},e=>e===missing||e===reference);
 expect(result.installed).toBe(2);expect(result.undeclared).toEqual([undeclared]);expect(result.missing).toEqual([missing]);expect(result.attention).toEqual([missing,reference]);
});
it('orders workflow references by real draft edit time, omits unreferenced packs',()=>{
 const old=entry('old',null,10),recent=entry('recent',null,30),unused=entry('unused',null,0);
 expect(localOverview([old,unused,recent],'node',{},()=>false).recent).toEqual([recent,old]);
});
