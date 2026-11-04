import {describe, it, expect} from 'vitest';
import type {CatalogPack, WorkflowGraph} from '../wire';
import {samePorts, replacementBaselineFromGraph, replacementCandidates, replacementCandidatesFromConnections, replaceAlgorithm} from './algorithmReplacement';
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
  it('uses complete graph port declarations when the historical pack is gone',()=>{
    const current = {...pack('demo','1.0.0'), manifest_hash:''};
    const portTypes = {schemaVersion:1, inputs:{in:{declaration:{tags:['image'],arrayed:true,scalar:false,dim_labels:['frame'],require_axis_label_match:true}}}, outputs:{out:{declaration:{tags:['image'],arrayed:false,scalar:false,dim_labels:[],dim_labels_drop_outer:0}}}};
    const baseline = replacementBaselineFromGraph(current, portTypes, false, false);
    expect(baseline).not.toBeNull();
    const matching = {...pack('demo','2.0.0'), inputs:{in:{...pack().inputs.in, require_axis_label_match:true}}, outputs:{out:{...pack().outputs.out, dim_labels_drop_outer:0}}};
    expect(replacementCandidates(baseline!, [matching, {...pack('wrong'), outputs:{out:{...pack().outputs.out, arrayed:true, dim_labels:['frame']}}}]).map(p=>p.version)).toEqual(['2.0.0']);
    expect(replacementBaselineFromGraph(current, portTypes, true, false)).toBeNull();
    expect(replacementBaselineFromGraph(current, portTypes, false)).toBeNull();
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
  it('offers a clearly weaker replacement only when every connected port is compatible',()=>{
    const historical={...pack('community/images-array-source'),inputs:{},outputs:{images:{tags:['image'],arrayed:false,scalar:false}},params:{}} as unknown as CatalogPack;
    const builtin={...historical,name:'builtin/images-array-source',version:'0.1.1',inputs:{optional:{tags:['float'],required:false}}} as unknown as CatalogPack;
    const wrongName={...builtin,name:'builtin/wrong',outputs:{other:builtin.outputs.images}} as unknown as CatalogPack;
    const wrongType={...builtin,name:'builtin/wrong-type',outputs:{images:{...builtin.outputs.images,tags:['video']}}} as unknown as CatalogPack;
    const consumer={...pack('sink'),inputs:{images:{tags:['image'],arrayed:false,scalar:false,required:true}},outputs:{},params:{}} as unknown as CatalogPack;
    const graph: WorkflowGraph={nodes:[
      {id:'source',algorithm_name:'source',algorithm_version:'1.0.0',position:{x:0,y:0},params:{},assigned_node_id:null},
      {id:'historical',algorithm_name:'community/images-array-source',algorithm_version:'0.1.1',position:{x:0,y:0},params:{},assigned_node_id:null},
      {id:'sink',algorithm_name:'sink',algorithm_version:'1.0.0',position:{x:0,y:0},params:{},assigned_node_id:null},
    ],edges:[{id:'edge',source:'historical',sourceHandle:'images',target:'sink',targetHandle:'images'}]};
    expect(replacementCandidatesFromConnections('historical',graph,[builtin,wrongName,wrongType,consumer]).map(p=>p.name)).toEqual(['builtin/images-array-source']);
    const node={id:'historical',position:{x:4,y:9},data:{pack:historical,replacementBaseline:historical,replacementMode:'connections' as const,params:{},assigned_node_id:null}};
    expect((replaceAlgorithm(node,builtin,true).data as any).pack.name).toBe('builtin/images-array-source');
    expect((replaceAlgorithm(node,builtin).data as any).pack.name).toBe('community/images-array-source');
  });
  it('does not infer a replacement from a node with no observed ports',()=>{
    const historical={...pack('community/gone'),inputs:{},outputs:{images:{tags:['image']}},params:{}} as unknown as CatalogPack;
    const graph: WorkflowGraph={nodes:[{id:'gone',algorithm_name:'community/gone',algorithm_version:'1.0.0',position:{x:0,y:0},params:{},assigned_node_id:null}],edges:[]};
    expect(replacementCandidatesFromConnections('gone',graph,[historical,{...historical,name:'builtin/gone'}])).toEqual([]);
  });
});
