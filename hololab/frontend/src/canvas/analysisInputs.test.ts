import {expect,it} from 'vitest';
import {analysisSignatures,mainJobsSignature,semanticGraph,stable} from './analysisInputs';
import {applyStaleness} from './applyStaleness';
const graph:any={nodes:[{id:'a',kind:'algorithm',algorithm_name:'foo',algorithm_version:'1.0.0',params:{x:null},position:{x:0,y:0}}],edges:[]};
it('normalizes object keys but preserves array order and explicit empties',()=>{
 expect(stable({b:1,a:2})).toBe(stable({a:2,b:1}));
 expect(new Set([null,[],{},'',undefined].map(x=>stable({x}))).size).toBe(5);
 expect(stable([1,2])).not.toBe(stable([2,1]));
});
it('ignores view nodes, view edges, positions and previews, not execution fields',()=>{
 const edited=structuredClone(graph);edited.nodes[0].position.x=500;edited.nodes[0].preview_open='out';
 edited.nodes.push({id:'view',kind:'view'});edited.edges.push({source:'a',target:'view'});
 expect(stable(semanticGraph(edited))).toBe(stable(semanticGraph(graph)));
 for(const field of ['params','port_values','assigned_node_id','arrayed_toggle','parallelism','batch_size']){
  const g=structuredClone(graph);g.nodes[0][field]='changed';expect(stable(semanticGraph(g))).not.toBe(stable(semanticGraph(graph)));
 }
});
it('filters unrelated catalog, canonicalizes aliases, retains selected device context',()=>{
 const context:any={snapshotId:null,snapshotGraph:null,jobs:[],devices:[],catalog:[],reconnect:0};
 const key=analysisSignatures(graph,context);
 expect(analysisSignatures(graph,{...context,catalog:[{name:'other',version:'1.0.0'}]})).toEqual(key);
 const alias=structuredClone(graph);alias.nodes[0].algorithm_name='kiri/foo';
 expect(analysisSignatures(alias,context)).toEqual(key);
 expect(analysisSignatures(graph,{...context,catalog:[{name:'foo',version:'1.0.0',node_ids:[],params:{}}]})).not.toEqual(key);
});
it('only latest main job semantic state and frozen configuration invalidate',()=>{
 const job:any={job_id:'j',graph_node_id:'a',algorithm_name:'foo',state:'pending',created_ts:1,params:{}};
 const base=mainJobsSignature([job]);
 for(const state of ['assigned','running','orphaned'])expect(mainJobsSignature([{...job,state,progress:99,updated_ts:999}])).toEqual(base);
 expect(mainJobsSignature([job,{...job,job_id:'shard',parent_job_id:'j',created_ts:9}])).toEqual(base);
 for(const patch of [{state:'done'},{state:'failed'},{job_id:'new'},{params:{x:2}},{analysis_params_pending:true}])expect(mainJobsSignature([{...job,...patch}])).not.toEqual(base);
});
it('preserves array and unaffected nodes, but updates changed reasons',()=>{
 const nodes=[{id:'a',data:{staleness:{kind:'self_dirty',title:'dirty',changeReasons:['x']}}},{id:'b',data:{}}];
 expect(applyStaleness(nodes,{a:{kind:'self_dirty',title:'dirty',changeReasons:['x']}})).toBe(nodes);
 const next=applyStaleness(nodes,{a:{kind:'self_dirty',title:'dirty',changeReasons:['y']}});
 expect(next[0]).not.toBe(nodes[0]);expect(next[1]).toBe(nodes[1]);
});
it('invalidates the shared analysis when variable declarations, local values or bindings change',()=>{
 const initial={...structuredClone(graph),variables:{dataset_dir:{type:'path',default:''}},variable_values:{dataset_dir:'/first'}};
 const key=analysisSignatures(initial).edit;
 const value=structuredClone(initial);value.variable_values.dataset_dir='/second';
 expect(analysisSignatures(value).edit).not.toBe(key);
 const declaration=structuredClone(initial);declaration.variables.dataset_dir.default='/default';
 expect(analysisSignatures(declaration).edit).not.toBe(key);
 const binding=structuredClone(initial);binding.nodes[0].param_bindings={path:'dataset_dir'};
 expect(analysisSignatures(binding).edit).not.toBe(key);
});
