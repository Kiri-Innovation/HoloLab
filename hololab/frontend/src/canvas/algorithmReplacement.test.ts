import {describe, it, expect} from 'vitest';
import type {CatalogPack} from '../wire';
import {samePorts, replacementCandidates, replaceAlgorithm} from './algorithmReplacement';
const pack = (name='demo', version='1.0.0') => ({name, version, inputs:{in:{tags:['image'],arrayed:true,dim_labels:['frame']}}, outputs:{out:{tags:['image']}}, params:{count:{type:'int',default:2},mode:{type:'enum',values:['a','b'],default:'a'},old:{type:'string',default:''}},node_ids:[]} as unknown as CatalogPack);
describe('algorithm replacement',()=>{
  it('compares complete named declarations, not connection compatibility',()=>{
    const a=pack(),b=pack('other'); expect(samePorts(a,b)).toBe(true);
    for (const change of [{scalar:true},{dim_labels:['camera']},{arrayed:false},{tags:['image','extra']},{require_axis_label_match:false}]) {
      expect(samePorts(a,{...b,inputs:{in:{...b.inputs.in,...change}}})).toBe(false);
    }
    expect(samePorts(a,{...b,inputs:{renamed:b.inputs.in}})).toBe(false);
    expect(samePorts(a,{...b,arrayable:true})).toBe(false);
    expect(samePorts(a,{...b,outputs:{out:{...b.outputs.out,preview:{viewer:'image',member:null}}}})).toBe(true);
  });
  it('sorts same algorithm versions first and excludes incompatible packs',()=>{
    expect(replacementCandidates(pack(),[pack('other'),pack('demo','2.0.0'),pack('demo','10.0.0'),{...pack('bad'),inputs:{}}]).map(p=>p.name+'@'+p.version)).toEqual(['demo@10.0.0','demo@2.0.0','other@1.0.0']);
  });
  it('orders releases before prereleases and migrates effective defaults',()=>{
    expect(replacementCandidates(pack(),[pack('demo','2.0.0-rc.1'),pack('demo','2.0.0'),pack('demo','2.0.0-beta')]).map(p=>p.version)).toEqual(['2.0.0','2.0.0-rc.1','2.0.0-beta']);
    const source={id:'n',position:{x:0,y:0},data:{pack:pack(),params:{},assigned_node_id:null}};
    const target=pack('demo','2');target.params.count.default=99;
    expect((replaceAlgorithm(source,target).data.params as Record<string,unknown>).count).toBe(2);
  });
  it('retains identity, layout, results for drift and compatible values; uses new defaults',()=>{
    const source={id:'semantic-node',position:{x:4,y:9},data:{pack:pack(),params:{count:12,mode:'b',old:'remove'},assigned_node_id:'device',previews:{out:{snapshot_id:'historical'}}}};
    const target={...pack('demo','2'),params:{count:{type:'float',default:3},mode:{type:'enum',values:['a'],default:'a'},new:{type:'bool',default:true}}} as unknown as CatalogPack;
    const result=replaceAlgorithm(source,target);
    expect(result.id).toBe(source.id);expect(result.position).toEqual(source.position);
    expect(result.data.params).toEqual({count:12,mode:'a',new:true});
    expect(result.data.previews).toEqual(source.data.previews);
    expect(source.data.params.old).toBe('remove');
    expect(replaceAlgorithm(source,{...target,outputs:{}})).toBe(source);
  });
});
